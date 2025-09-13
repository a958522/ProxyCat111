import asyncio
import aiohttp
import json
import time
import logging
import ipaddress
from datetime import datetime, timedelta
from typing import Set, Optional, Dict, Any
import requests
from modules.modules import get_message, load_config

class CountryBasedProxyManager:
    """基于国家检测的智能代理管理器"""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.language = config.get('language', 'cn')
        self.target_country = config.get('target_country', 'US')  # 目标国家代码
        self.check_interval = int(config.get('country_check_interval', 60))  # 检测间隔(秒)
        self.max_retries = int(config.get('max_retries', 3))  # 最大重试次数
        self.timeout = int(config.get('request_timeout', 10))  # 请求超时时间
        
        # IP黑名单相关
        self.blacklist_url = config.get('ip_blacklist_url', 'https://syncnote.fycloud.online/block')
        self.blacklist_update_interval = 24 * 3600  # 24小时更新一次黑名单
        self.ip_blacklist: Set[str] = set()
        self.blacklist_last_update = 0
        
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
            'blacklist_hits': 0
        }
    
    async def update_ip_blacklist(self) -> bool:
        """更新IP黑名单"""
        current_time = time.time()
        if current_time - self.blacklist_last_update < self.blacklist_update_interval:
            return True
            
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
                async with session.get(self.blacklist_url) as response:
                    if response.status == 200:
                        content = await response.text()
                        # 解析IP地址和IP段
                        new_blacklist = set()
                        for line in content.strip().split('\n'):
                            line = line.strip()
                            if line and not line.startswith('#'):
                                new_blacklist.add(line)
                        
                        self.ip_blacklist = new_blacklist
                        self.blacklist_last_update = current_time
                        logging.info(f"IP黑名单已更新，共 {len(self.ip_blacklist)} 条记录")
                        return True
                    else:
                        logging.error(f"更新IP黑名单失败，HTTP状态码: {response.status}")
                        return False
                        
        except Exception as e:
            logging.error(f"更新IP黑名单时发生错误: {e}")
            return False
    
    def is_ip_blacklisted(self, ip: str) -> bool:
        """检查IP是否在黑名单中"""
        try:
            ip_obj = ipaddress.ip_address(ip)
            
            for blacklist_entry in self.ip_blacklist:
                try:
                    # 检查是否是IP段
                    if '/' in blacklist_entry:
                        network = ipaddress.ip_network(blacklist_entry, strict=False)
                        if ip_obj in network:
                            return True
                    # 检查是否是单个IP
                    elif ip == blacklist_entry:
                        return True
                except ValueError:
                    # 忽略无效的IP格式
                    continue
            
            return False
            
        except ValueError:
            # 无效的IP地址
            return False
    
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
                async with session.get('https://ipinfo.io?token=2247bca03780c6') as response:
                    if response.status == 200:
                        data = await response.json()
                        country = data.get('country')
                        ip = data.get('ip')
                        
                        if country and ip:
                            # 检查IP是否在黑名单中
                            if self.is_ip_blacklisted(ip):
                                logging.warning(f"落地IP {ip} 在黑名单中，国家: {country}")
                                self.stats['blacklist_hits'] += 1
                                return 'BLACKLISTED'
                            
                            logging.info(f"代理 {proxy_host}:{proxy_port} 落地IP: {ip}, 国家: {country}")
                            return country
                        else:
                            logging.warning(f"ipinfo.io响应缺少country或ip字段: {data}")
                            return None
                    else:
                        logging.error(f"ipinfo.io请求失败，状态码: {response.status}")
                        return None
                        
        except Exception as e:
            logging.error(f"获取代理国家信息时发生错误: {e}")
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
            # 无法获取国家信息，可能是代理失效
            logging.warning("无法获取代理国家信息，可能是代理失效")
            return True, None
        
        if country == 'BLACKLISTED':
            # IP在黑名单中，需要切换
            logging.warning("当前代理落地IP在黑名单中，需要切换")
            return True, country
        
        # 记录当前国家
        if self.current_country != country:
            if self.current_country is not None:
                logging.info(f"检测到国家变化: {self.current_country} -> {country}")
                self.stats['country_changes'] += 1
            self.current_country = country
        
        # 检查是否需要切换代理
        if country != self.target_country:
            logging.warning(f"当前国家 {country} 不符合目标国家 {self.target_country}，需要切换代理")
            return True, country
        
        return False, country
    
    async def start_monitoring(self, get_new_proxy_func, switch_proxy_func):
        """开始监控代理国家变化"""
        self.is_monitoring = True
        logging.info(f"开始监控代理国家变化，目标国家: {self.target_country}, 检测间隔: {self.check_interval}秒")
        
        while self.is_monitoring:
            try:
                if self.current_proxy:
                    should_switch, detected_country = await self.check_proxy_country_change(self.current_proxy)
                    
                    if should_switch:
                        logging.info("触发代理切换...")
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
                                        logging.warning(f"新代理IP在黑名单中，重试获取 ({retries + 1}/{self.max_retries})")
                                    else:
                                        logging.warning(f"新代理国家 {new_country} 不符合目标 {self.target_country}，重试获取 ({retries + 1}/{self.max_retries})")
                                
                                retries += 1
                                if retries < self.max_retries:
                                    await asyncio.sleep(2)  # 重试间隔
                                    
                            except Exception as e:
                                logging.error(f"获取新代理时发生错误: {e}")
                                retries += 1
                                if retries < self.max_retries:
                                    await asyncio.sleep(2)
                        
                        if new_proxy:
                            # 切换代理
                            old_proxy = self.current_proxy
                            await switch_proxy_func(new_proxy)
                            self.current_proxy = new_proxy
                            logging.info(f"代理已切换: {old_proxy} -> {new_proxy}")
                        else:
                            logging.error(f"在 {self.max_retries} 次重试后仍无法获取符合要求的代理")
                
                # 等待下次检测
                await asyncio.sleep(self.check_interval)
                
            except Exception as e:
                logging.error(f"监控过程中发生错误: {e}")
                await asyncio.sleep(10)  # 错误后短暂等待
    
    def stop_monitoring(self):
        """停止监控"""
        self.is_monitoring = False
        logging.info("代理国家监控已停止")
    
    def set_current_proxy(self, proxy: str):
        """设置当前代理"""
        self.current_proxy = proxy
        self.current_country = None  # 重置国家状态
    
    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        return {
            **self.stats,
            'current_proxy': self.current_proxy,
            'current_country': self.current_country,
            'target_country': self.target_country,
            'blacklist_size': len(self.ip_blacklist),
            'last_blacklist_update': datetime.fromtimestamp(self.blacklist_last_update).isoformat() if self.blacklist_last_update else None,
            'is_monitoring': self.is_monitoring
        }

# 修改后的 getip.py 中的 newip 函数
def newip_with_country_check():
    """增强版的获取代理函数，包含国家检测"""
    config = load_config()
    language = config.get('language', 'cn')
    
    def handle_error(error_type, details=None):
        error_msg = 'whitelist_error' if error_type == 'whitelist' else 'proxy_file_not_found'
        print(get_message(error_msg, language, str(details)))
        raise ValueError(f"{error_type}: {details}")
    
    try:
        # 配置信息
        list_url = config.get('getip_url', '')
        buy_url_template = config.get('buy_url_template', 'https://api.s5proxies.com/api2.php?do=buy&key=68bdb7530a84a2025090711481968bdb7530a85a&id=')
        username = config.get('proxy_username', '')
        password = config.get('proxy_password', '')
        target_country = config.get('target_country', 'US')
        max_country_retries = int(config.get('max_country_retries', 5))
        
        if not list_url:
            raise ValueError('getip_url')
        
        def get_proxy_list():
            """获取代理列表并随机选择一个ID"""
            response = requests.get(list_url)
            response.raise_for_status()
            
            try:
                data = json.loads(response.text)
                
                if data.get('status', {}).get('code') != '1000':
                    error_msg = data.get('status', {}).get('message', 'Unknown API error')
                    raise ValueError(f"获取代理列表API返回错误: {error_msg}")
                
                proxy_list = data.get('data', [])
                
                if not proxy_list:
                    raise ValueError("代理列表为空")
                
                # 过滤代理
                filtered_proxy_list = []
                for proxy in proxy_list:
                    host = proxy.get('host', '').lower()
                    if 'verizon' not in host and 'rcn' not in host:
                        filtered_proxy_list.append(proxy)
                
                if not filtered_proxy_list:
                    raise ValueError("过滤后的代理列表为空")
                
                print(f"原始代理数量: {len(proxy_list)}, 过滤后数量: {len(filtered_proxy_list)}")
                
                import random
                selected_proxy = random.choice(filtered_proxy_list)
                proxy_id = selected_proxy.get('id')
                
                if not proxy_id:
                    raise ValueError("选中的代理缺少ID信息")
                
                print(f"随机选择的代理ID: {proxy_id}")
                print(f"代理位置: {selected_proxy.get('city', 'Unknown')}, {selected_proxy.get('region', 'Unknown')}")
                print(f"Host: {selected_proxy.get('host', 'Unknown')}")
                
                return proxy_id
                
            except json.JSONDecodeError:
                raise ValueError("获取代理列表API响应不是有效的JSON格式")
        
        def buy_proxy(proxy_id):
            """通过ID购买/获取具体代理信息"""
            buy_url = f"{buy_url_template}{proxy_id}"
            print(f"获取代理详情: {buy_url}")
            
            response = requests.get(buy_url)
            response.raise_for_status()
            
            try:
                data = json.loads(response.text)
                
                if data.get('status', {}).get('code') != '1000':
                    error_msg = data.get('status', {}).get('message', 'Unknown API error')
                    raise ValueError(f"获取代理详情API返回错误: {error_msg}")
                
                proxy_data = data.get('data', {})
                ip = proxy_data.get('ipaddress')
                port = proxy_data.get('port')
                
                if not ip or not port:
                    raise ValueError("API响应中缺少IP或端口信息")
                
                print(f"获取到代理: {ip}:{port}")
                return f"{ip}:{port}"
                
            except json.JSONDecodeError:
                raise ValueError("获取代理详情API响应不是有效的JSON格式")
        
        def check_proxy_country(proxy_str):
            """检查代理的落地国家"""
            try:
                import subprocess
                import json
                
                # 构造curl命令
                if username and password:
                    proxy_url = f"socks5://{username}:{password}@{proxy_str}"
                else:
                    proxy_url = f"socks5://{proxy_str}"
                
                cmd = [
                    'curl', '-x', proxy_url,
                    'https://ipinfo.io?token=2247bca03780c6',
                    '--connect-timeout', '10',
                    '--max-time', '15',
                    '-s'
                ]
                
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
                
                if result.returncode == 0:
                    data = json.loads(result.stdout)
                    country = data.get('country')
                    ip = data.get('ip')
                    print(f"代理 {proxy_str} 落地IP: {ip}, 国家: {country}")
                    return country
                else:
                    print(f"curl命令执行失败: {result.stderr}")
                    return None
                    
            except Exception as e:
                print(f"检查代理国家时发生错误: {e}")
                return None
        
        # 尝试获取符合目标国家的代理
        for attempt in range(max_country_retries):
            proxy_id = get_proxy_list()
            proxy = buy_proxy(proxy_id)
            
            # 检查代理国家
            country = check_proxy_country(proxy)
            
            if country == target_country:
                print(f"✓ 获取到符合目标国家 {target_country} 的代理: {proxy}")
                break
            elif country:
                print(f"✗ 代理国家 {country} 不符合目标国家 {target_country}，重试中... ({attempt + 1}/{max_country_retries})")
            else:
                print(f"✗ 无法检测代理国家，重试中... ({attempt + 1}/{max_country_retries})")
            
            if attempt < max_country_retries - 1:
                time.sleep(2)  # 重试间隔
        else:
            print(f"警告: 在 {max_country_retries} 次尝试后仍未获取到目标国家的代理，使用最后一个代理")
        
        # 处理特殊错误代码
        if proxy == "error000x-13":
            appKey = ""
            anquanma = ""
            whitelist_url = f"https://sch.shanchendaili.com/api.html?action=addWhiteList&appKey={appKey}&anquanma={anquanma}"
            requests.get(whitelist_url).raise_for_status()
            time.sleep(1)
            proxy_id = get_proxy_list()
            proxy = buy_proxy(proxy_id)
        
        # 返回完整的代理URL
        if username and password:
            return f"socks5://{username}:{password}@{proxy}"
        return f"socks5://{proxy}"
        
    except requests.RequestException as e:
        handle_error('request', e)
    except ValueError as e:
        handle_error('config', e)
    except Exception as e:
        handle_error('unknown', e)

# 新的代理函数，直接替换原来的 newip
def newip():
    """新的 newip 函数，集成国家检测功能"""
    return newip_with_country_check()




