# ===== 1. 修改 modules/country_proxy_manager.py =====

import asyncio
import aiohttp
import json
import time
import logging
import ipaddress
import os
from datetime import datetime, timedelta
from typing import Set, Optional, Dict, Any
import requests

class CountryBasedProxyManager:
    """基于国家检测的智能代理管理器 - 增强本地缓存版"""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.language = config.get('language', 'cn')
        self.target_country = config.get('target_country', 'US')
        self.check_interval = int(config.get('country_check_interval', 60))
        self.max_retries = int(config.get('max_retries', 3))
        self.timeout = int(config.get('request_timeout', 10))
        
        # 黑名单相关配置
        self.blacklist_url = config.get('ip_blacklist_url', '')
        self.enable_blacklist = config.get('enable_ip_blacklist', 'True').lower() == 'true'
        
        # 本地缓存配置
        self.blacklist_cache_dir = os.path.join('config', 'cache')
        self.blacklist_cache_file = os.path.join(self.blacklist_cache_dir, 'ip_blacklist.txt')
        self.blacklist_meta_file = os.path.join(self.blacklist_cache_dir, 'blacklist_meta.json')
        
        # 更新间隔：默认24小时，可从配置读取
        self.blacklist_update_interval = int(config.get('blacklist_update_interval', 86400))
        
        # 确保缓存目录存在
        os.makedirs(self.blacklist_cache_dir, exist_ok=True)
        
        # 黑名单数据
        self.ip_blacklist: Set[str] = set()
        self.blacklist_last_update = 0
        self.blacklist_loaded = False
        
        # 当前代理状态
        self.current_proxy = None
        self.current_country = None
        self.last_check_time = 0
        self.is_monitoring = False
        
        # 统计信息
        self.stats = {
            'total_checks': 0,
            'country_changes': 0,
            'proxy_switches': 0,
            'blacklist_hits': 0,
            'blacklist_source': 'unknown'  # 'local', 'remote', 'empty'
        }
        
        # 初始化时加载黑名单
        self._init_blacklist()
    
    def _init_blacklist(self):
        """初始化黑名单 - 优先加载本地缓存"""
        if not self.enable_blacklist:
            logging.info("🚫 IP黑名单功能已禁用")
            return
        
        try:
            # 1. 首先尝试从本地加载
            if self._load_local_blacklist():
                logging.info(f"📋 从本地缓存加载黑名单成功，共 {len(self.ip_blacklist)} 条记录")
                self.stats['blacklist_source'] = 'local'
                self.blacklist_loaded = True
                
                # 检查是否需要更新
                if self._should_update_blacklist():
                    logging.info("⏰ 本地黑名单需要更新，开始后台下载...")
                    # 在后台异步更新（不阻塞主程序）
                    asyncio.create_task(self._background_update_blacklist())
                else:
                    hours_old = (time.time() - self.blacklist_last_update) / 3600
                    logging.info(f"✅ 本地黑名单较新，无需更新 (上次更新: {hours_old:.1f}小时前)")
            
            else:
                # 2. 本地加载失败，尝试立即从远程下载
                logging.warning("⚠️ 本地黑名单加载失败，尝试从远程下载...")
                if self.blacklist_url:
                    success = self._sync_download_blacklist()
                    if success:
                        self.stats['blacklist_source'] = 'remote'
                        self.blacklist_loaded = True
                        logging.info("✅ 远程黑名单下载成功")
                    else:
                        logging.error("❌ 远程黑名单下载失败，黑名单功能将不可用")
                        self.stats['blacklist_source'] = 'empty'
                else:
                    logging.warning("⚠️ 未配置黑名单URL，黑名单功能将不可用")
                    self.stats['blacklist_source'] = 'empty'
        
        except Exception as e:
            logging.error(f"❌ 黑名单初始化失败: {e}")
            self.stats['blacklist_source'] = 'empty'
    
    def _load_local_blacklist(self) -> bool:
        """从本地文件加载黑名单"""
        try:
            if not os.path.exists(self.blacklist_cache_file):
                logging.debug("📁 本地黑名单文件不存在")
                return False
            
            # 读取元数据
            meta_info = self._load_blacklist_meta()
            
            # 读取黑名单内容
            with open(self.blacklist_cache_file, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # 解析黑名单
            new_blacklist = set()
            valid_count = 0
            for line in content.strip().split('\n'):
                line = line.strip()
                if line and not line.startswith('#') and line != '':
                    if self._validate_ip_entry(line):
                        new_blacklist.add(line)
                        valid_count += 1
                    else:
                        logging.debug(f"忽略无效IP格式: {line}")
            
            if new_blacklist:
                self.ip_blacklist = new_blacklist
                self.blacklist_last_update = meta_info.get('last_update', 0)
                logging.debug(f"📁 本地黑名单解析完成: {valid_count} 条有效记录")
                return True
            else:
                logging.warning("⚠️ 本地黑名单文件内容为空或无效")
                return False
        
        except Exception as e:
            logging.error(f"❌ 加载本地黑名单失败: {e}")
            return False
    
    def _load_blacklist_meta(self) -> dict:
        """加载黑名单元数据"""
        try:
            if os.path.exists(self.blacklist_meta_file):
                with open(self.blacklist_meta_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception as e:
            logging.debug(f"加载黑名单元数据失败: {e}")
        
        return {}
    
    def _save_blacklist_meta(self, meta_info: dict):
        """保存黑名单元数据"""
        try:
            with open(self.blacklist_meta_file, 'w', encoding='utf-8') as f:
                json.dump(meta_info, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logging.error(f"❌ 保存黑名单元数据失败: {e}")
    
    def _should_update_blacklist(self) -> bool:
        """检查是否需要更新黑名单"""
        if self.blacklist_last_update == 0:
            return True
        
        current_time = time.time()
        time_since_update = current_time - self.blacklist_last_update
        return time_since_update >= self.blacklist_update_interval
    
    def _sync_download_blacklist(self) -> bool:
        """同步下载黑名单（用于初始化）"""
        try:
            if not self.blacklist_url:
                logging.warning("⚠️ 黑名单URL未配置")
                return False
            
            logging.info(f"📥 正在同步下载黑名单: {self.blacklist_url}")
            response = requests.get(self.blacklist_url, timeout=30)
            
            if response.status_code == 200:
                content = response.text
                if content.strip():
                    return self._save_blacklist_content(content, 'remote_sync')
                else:
                    logging.warning("⚠️ 下载的黑名单内容为空")
                    return False
            else:
                logging.error(f"❌ 同步下载黑名单失败，状态码: {response.status_code}")
                return False
                
        except Exception as e:
            logging.error(f"❌ 同步下载黑名单异常: {e}")
            return False
    
    async def _background_update_blacklist(self):
        """后台异步更新黑名单"""
        try:
            if not self.blacklist_url:
                return
            
            logging.info(f"🔄 开始后台更新黑名单: {self.blacklist_url}")
            
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
                async with session.get(self.blacklist_url) as response:
                    if response.status == 200:
                        content = await response.text()
                        if content.strip():
                            if self._save_blacklist_content(content, 'remote_async'):
                                logging.info("✅ 后台黑名单更新成功")
                                self.stats['blacklist_source'] = 'remote'
                            else:
                                logging.error("❌ 后台黑名单保存失败")
                        else:
                            logging.warning("⚠️ 后台下载的黑名单内容为空")
                    else:
                        logging.error(f"❌ 后台黑名单下载失败，状态码: {response.status}")
        
        except asyncio.CancelledError:
            logging.info("🛑 后台黑名单更新被取消")
        except Exception as e:
            logging.error(f"❌ 后台更新黑名单异常: {e}")
    
    def _save_blacklist_content(self, content: str, source: str) -> bool:
        """保存黑名单内容到本地"""
        try:
            # 解析并验证内容
            new_blacklist = set()
            valid_count = 0
            invalid_count = 0
            
            for line in content.strip().split('\n'):
                line = line.strip()
                if line and not line.startswith('#') and line != '':
                    if self._validate_ip_entry(line):
                        new_blacklist.add(line)
                        valid_count += 1
                    else:
                        invalid_count += 1
                        logging.debug(f"忽略无效黑名单条目: {line}")
            
            if not new_blacklist:
                logging.warning("⚠️ 解析后的黑名单内容为空")
                return False
            
            # 保存原始内容到本地文件
            with open(self.blacklist_cache_file, 'w', encoding='utf-8') as f:
                f.write(content)
            
            # 保存元数据
            current_time = time.time()
            meta_info = {
                'last_update': current_time,
                'source': source,
                'valid_count': valid_count,
                'invalid_count': invalid_count,
                'update_time': datetime.now().isoformat(),
                'url': self.blacklist_url
            }
            self._save_blacklist_meta(meta_info)
            
            # 更新内存中的黑名单
            old_size = len(self.ip_blacklist)
            self.ip_blacklist = new_blacklist
            self.blacklist_last_update = current_time
            self.blacklist_loaded = True
            
            logging.info(f"💾 黑名单已更新: {old_size} -> {valid_count} 条记录 (来源: {source})")
            
            if invalid_count > 0:
                logging.warning(f"⚠️ 忽略了 {invalid_count} 条无效记录")
            
            return True
        
        except Exception as e:
            logging.error(f"❌ 保存黑名单内容失败: {e}")
            return False
    
    def _validate_ip_entry(self, entry: str) -> bool:
        """验证IP条目格式"""
        if not entry or entry.startswith('#'):
            return False
        
        try:
            if '/' in entry:
                # IP网段
                ipaddress.ip_network(entry, strict=False)
            else:
                # 单个IP
                ipaddress.ip_address(entry)
            return True
        except ValueError:
            return False
    
    async def update_ip_blacklist(self) -> bool:
        """手动更新IP黑名单（保持原接口兼容性）"""
        if not self.enable_blacklist:
            logging.info("🚫 黑名单功能已禁用，跳过更新")
            return True
        
        if self._should_update_blacklist():
            logging.info("🔄 手动触发黑名单更新...")
            await self._background_update_blacklist()
            return True
        else:
            hours_since_update = (time.time() - self.blacklist_last_update) / 3600
            logging.info(f"⏭️ 黑名单无需更新，距离上次更新仅 {hours_since_update:.1f} 小时")
            return True
    
    def is_ip_blacklisted(self, ip: str) -> bool:
        """检查IP是否在黑名单中"""
        if not self.enable_blacklist or not self.blacklist_loaded:
            return False
        
        try:
            ip_obj = ipaddress.ip_address(ip)
            
            for blacklist_entry in self.ip_blacklist:
                try:
                    if '/' in blacklist_entry:
                        # IP网段检查
                        network = ipaddress.ip_network(blacklist_entry, strict=False)
                        if ip_obj in network:
                            logging.debug(f"🚫 IP {ip} 匹配黑名单网段: {blacklist_entry}")
                            return True
                    elif ip == blacklist_entry:
                        # 单个IP检查
                        logging.debug(f"🚫 IP {ip} 在黑名单中")
                        return True
                except ValueError:
                    continue
            
            return False
            
        except ValueError:
            logging.debug(f"⚠️ 无效IP地址格式: {ip}")
            return False
    
    def get_blacklist_stats(self) -> dict:
        """获取黑名单详细统计信息"""
        meta_info = self._load_blacklist_meta()
        
        return {
            'enabled': self.enable_blacklist,
            'loaded': self.blacklist_loaded,
            'size': len(self.ip_blacklist),
            'last_update': datetime.fromtimestamp(self.blacklist_last_update).isoformat() if self.blacklist_last_update else None,
            'source': self.stats['blacklist_source'],
            'cache_file_exists': os.path.exists(self.blacklist_cache_file),
            'meta_file_exists': os.path.exists(self.blacklist_meta_file),
            'hours_since_update': (time.time() - self.blacklist_last_update) / 3600 if self.blacklist_last_update else 0,
            'needs_update': self._should_update_blacklist(),
            'update_interval_hours': self.blacklist_update_interval / 3600,
            'url': self.blacklist_url,
            'meta_info': meta_info
        }
    
    def force_update_blacklist(self):
        """强制更新黑名单（重置更新时间）"""
        self.blacklist_last_update = 0
        return asyncio.create_task(self._background_update_blacklist())
    
    # ===== 保持原有的其他方法不变 =====
    
    async def get_proxy_country(self, proxy: str) -> Optional[str]:
        """获取代理的真实落地IP国家"""
        if not proxy:
            return None
            
        try:
            # 解析代理地址
            if '://' in proxy:
                proxy_parts = proxy.split('://', 1)[1]
            else:
                proxy_parts = proxy
                
            if '@' in proxy_parts:
                auth_part, addr_part = proxy_parts.split('@', 1)
                username, password = auth_part.split(':', 1)
                proxy_host, proxy_port = addr_part.split(':', 1)
            else:
                proxy_host, proxy_port = proxy_parts.split(':', 1)
                username = password = None
            
            # 构造curl命令的代理参数
            if username and password:
                proxy_url = f"socks5://{username}:{password}@{proxy_host}:{proxy_port}"
            else:
                proxy_url = f"socks5://{proxy_host}:{proxy_port}"
            
            # 使用aiohttp通过代理请求ipinfo.io
            connector = aiohttp.SocksConnector.from_url(proxy_url)
            timeout = aiohttp.ClientTimeout(total=self.timeout)
            
            async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
                async with session.get('https://ipinfo.io?token=68cdce81ca2b21') as response:
                    if response.status == 200:
                        data = await response.json()
                        country = data.get('country')
                        ip = data.get('ip')
                        
                        if country and ip:
                            # 检查IP是否在黑名单中
                            if self.is_ip_blacklisted(ip):
                                logging.warning(f"🚫 落地IP {ip} 在黑名单中，国家: {country}")
                                self.stats['blacklist_hits'] += 1
                                return 'BLACKLISTED'
                            
                            logging.info(f"🌐 代理 {proxy_host}:{proxy_port} 落地IP: {ip}, 国家: {country}")
                            return country
                        else:
                            logging.warning(f"⚠️ ipinfo.io响应缺少country或ip字段: {data}")
                            return None
                    else:
                        logging.error(f"❌ ipinfo.io请求失败，状态码: {response.status}")
                        return None
                        
        except Exception as e:
            logging.error(f"❌ 获取代理国家信息时发生错误: {e}")
            return None
    
    async def check_proxy_country_change(self, proxy: str) -> tuple[bool, Optional[str]]:
        """检查代理国家是否发生变化"""
        if not proxy:
            return False, None
            
        self.stats['total_checks'] += 1
        
        # 更新IP黑名单（如果需要）
        await self.update_ip_blacklist()
        
        country = await self.get_proxy_country(proxy)
        
        if country is None:
            logging.warning("⚠️ 无法获取代理国家信息，可能是代理失效")
            return True, None
        
        if country == 'BLACKLISTED':
            logging.warning("🚫 当前代理落地IP在黑名单中，需要切换")
            return True, country
        
        # 记录当前国家
        if self.current_country != country:
            if self.current_country is not None:
                logging.info(f"🌍 检测到国家变化: {self.current_country} -> {country}")
                self.stats['country_changes'] += 1
            self.current_country = country
        
        # 检查是否需要切换代理
        if country != self.target_country:
            logging.warning(f"⚠️ 当前国家 {country} 不符合目标国家 {self.target_country}，需要切换代理")
            return True, country
        
        return False, country
    
    async def start_monitoring(self, get_new_proxy_func, switch_proxy_func):
        """开始监控代理国家变化"""
        self.is_monitoring = True
        logging.info(f"🌍 开始监控代理国家变化，目标国家: {self.target_country}, 检测间隔: {self.check_interval}秒")
        
        while self.is_monitoring:
            try:
                if self.current_proxy:
                    should_switch, detected_country = await self.check_proxy_country_change(self.current_proxy)
                    
                    if should_switch:
                        logging.info("🔄 触发代理切换...")
                        self.stats['proxy_switches'] += 1
                        
                        # 获取新代理
                        retries = 0
                        new_proxy = None
                        
                        while retries < self.max_retries:
                            try:
                                new_proxy = await get_new_proxy_func()
                                if new_proxy:
                                    # 验证新代理的国家
                                    new_country = await self.get_proxy_country(new_proxy)
                                    if new_country == self.target_country:
                                        break
                                    elif new_country == 'BLACKLISTED':
                                        logging.warning(f"⚠️ 新代理IP在黑名单中，重试获取 ({retries + 1}/{self.max_retries})")
                                    else:
                                        logging.warning(f"⚠️ 新代理国家 {new_country} 不符合目标 {self.target_country}，重试获取 ({retries + 1}/{self.max_retries})")
                                
                                retries += 1
                                if retries < self.max_retries:
                                    await asyncio.sleep(2)
                                    
                            except Exception as e:
                                logging.error(f"❌ 获取新代理时发生错误: {e}")
                                retries += 1
                                if retries < self.max_retries:
                                    await asyncio.sleep(2)
                        
                        if new_proxy:
                            old_proxy = self.current_proxy
                            await switch_proxy_func(new_proxy)
                            self.current_proxy = new_proxy
                            logging.info(f"✅ 代理已切换: {old_proxy} -> {new_proxy}")
                        else:
                            logging.error(f"❌ 在 {self.max_retries} 次重试后仍无法获取符合要求的代理")
                
                # 等待下次检测
                await asyncio.sleep(self.check_interval)
                
            except Exception as e:
                logging.error(f"❌ 监控过程中发生错误: {e}")
                await asyncio.sleep(10)
    
    def stop_monitoring(self):
        """停止监控"""
        self.is_monitoring = False
        logging.info("🛑 代理国家监控已停止")
    
    def set_current_proxy(self, proxy: str):
        """设置当前代理"""
        self.current_proxy = proxy
        self.current_country = None
    
    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息（增强版）"""
        base_stats = {
            **self.stats,
            'current_proxy': self.current_proxy,
            'current_country': self.current_country,
            'target_country': self.target_country,
            'blacklist_size': len(self.ip_blacklist),
            'is_monitoring': self.is_monitoring
        }
        
        # 添加黑名单详细统计
        if self.enable_blacklist:
            blacklist_stats = self.get_blacklist_stats()
            base_stats.update({
                'blacklist_enabled': blacklist_stats['enabled'],
                'blacklist_loaded': blacklist_stats['loaded'],
                'blacklist_source': blacklist_stats['source'],
                'blacklist_needs_update': blacklist_stats['needs_update'],
                'blacklist_hours_since_update': blacklist_stats['hours_since_update']
            })
        else:
            base_stats.update({
                'blacklist_enabled': False,
                'blacklist_loaded': False,
                'blacklist_source': 'disabled',
                'blacklist_needs_update': False,
                'blacklist_hours_since_update': 0
            })
        
        return base_stats
