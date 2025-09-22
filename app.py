#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
ProxyCat 增强安全版本 - 添加Web面板访问后缀和SOCKS5认证
"""

import asyncio
import socket
import struct
import threading
import logging
import os
import sys
import json
import time
import signal
import importlib.util
import subprocess
import base64
import ipaddress
from datetime import datetime
from flask import Flask, request, jsonify, render_template_string, abort
from concurrent.futures import ThreadPoolExecutor
from functools import wraps

# 添加当前目录到Python路径
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, current_dir)

# 🔧 修复：确保 Flask 应用在所有路由定义之前创建
app = Flask(__name__)
flask_app = app  # 创建别名，保持兼容性

# 全局变量
current_proxy = None
socks_server = None
country_monitor = None
main_loop = None  # 主事件循环
executor = ThreadPoolExecutor(max_workers=4)  # 线程池

proxy_stats = {
    'current_proxy': None,
    'current_country': None,
    'total_checks': 0,
    'proxy_switches': 0,
    'country_changes': 0,
    'blacklist_hits': 0,
    'blacklist_size': 0,
    'target_country': 'US',
    'mode': 'country',
    'language': 'cn',
    'use_getip': True,
    'port': 1080,
    'web_port': 5000,
    'connections_count': 0,
    'bytes_transferred': 0
}

# ===== 新增安全配置全局变量 =====
security_config = {
    'web_access_suffix': '/admin-panel-2024',
    'socks5_username': 'proxyuser',
    'socks5_password': 'proxy123456',
    'web_allowed_ips': [],
    'enable_access_log': True
}

def load_simple_config():
    """加载简化配置（增强安全版）"""
    config_path = os.path.join(current_dir, 'config', 'config.ini')
    config = {
        'mode': 'country',
        'target_country': 'US',
        'language': 'cn',
        'use_getip': 'True',
        'port': '1080',
        'web_port': '5000',
        'getip_url': '',
        'buy_url_template': '',
        'proxy_username': '',
        'proxy_password': '',
        'country_check_interval': '60',
        'ip_blacklist_url': '',
        'enable_ip_blacklist': 'True',
        'blacklist_update_interval': '86400',
        # 新增安全配置
        'web_access_suffix': '/admin-panel-2024',
        'socks5_username': 'proxyuser',
        'socks5_password': 'proxy123456',
        'web_allowed_ips': '',
        'enable_access_log': 'True'
    }
    
    if os.path.exists(config_path):
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and '=' in line:
                        key, value = line.split('=', 1)
                        key = key.strip()
                        value = value.strip()
                        config[key] = value
        except Exception as e:
            logging.error(f"读取配置文件失败: {e}")
    
    # 🔧 环境变量覆盖配置文件设置
    config['web_access_suffix'] = os.getenv('WEB_ACCESS_SUFFIX', config['web_access_suffix'])
    config['socks5_username'] = os.getenv('SOCKS5_USERNAME', config['socks5_username'])
    config['socks5_password'] = os.getenv('SOCKS5_PASSWORD', config['socks5_password'])
    config['web_allowed_ips'] = os.getenv('WEB_ALLOWED_IPS', config['web_allowed_ips'])
    config['enable_access_log'] = os.getenv('ENABLE_ACCESS_LOG', config['enable_access_log'])
    
    return config

def init_security_config():
    """初始化安全配置"""
    global security_config
    config = load_simple_config()
    
    security_config['web_access_suffix'] = config.get('web_access_suffix', '/admin-panel-2024')
    security_config['socks5_username'] = config.get('socks5_username', 'proxyuser')
    security_config['socks5_password'] = config.get('socks5_password', 'proxy123456')
    security_config['enable_access_log'] = config.get('enable_access_log', 'True').lower() == 'true'
    
    # 解析允许的IP地址列表
    allowed_ips_str = config.get('web_allowed_ips', '')
    security_config['web_allowed_ips'] = []
    
    if allowed_ips_str.strip():
        for ip_str in allowed_ips_str.split(','):
            ip_str = ip_str.strip()
            if ip_str:
                try:
                    # 支持单个IP和CIDR网段
                    if '/' in ip_str:
                        network = ipaddress.ip_network(ip_str, strict=False)
                        security_config['web_allowed_ips'].append(network)
                    else:
                        ip = ipaddress.ip_address(ip_str)
                        security_config['web_allowed_ips'].append(ip)
                except ValueError:
                    logging.warning(f"⚠️ 无效的IP地址格式: {ip_str}")
    
    # 确保访问后缀以 / 开头
    if not security_config['web_access_suffix'].startswith('/'):
        security_config['web_access_suffix'] = '/' + security_config['web_access_suffix']
    
    logging.info(f"🔒 Web管理面板访问路径: {security_config['web_access_suffix']}")
    logging.info(f"🔐 SOCKS5认证: 用户名={security_config['socks5_username']}")
    if security_config['web_allowed_ips']:
        logging.info(f"🛡️ Web面板IP限制: {len(security_config['web_allowed_ips'])} 个允许的IP/网段")
    else:
        logging.info("🌐 Web面板无IP限制")

def check_web_access_permission():
    """检查Web访问权限"""
    if not security_config['web_allowed_ips']:
        return True  # 没有IP限制
    
    client_ip = request.environ.get('HTTP_X_FORWARDED_FOR', request.environ.get('REMOTE_ADDR', ''))
    if ',' in client_ip:
        client_ip = client_ip.split(',')[0].strip()
    
    try:
        client_addr = ipaddress.ip_address(client_ip)
        for allowed in security_config['web_allowed_ips']:
            if isinstance(allowed, ipaddress.IPv4Network) or isinstance(allowed, ipaddress.IPv6Network):
                if client_addr in allowed:
                    return True
            elif client_addr == allowed:
                return True
        
        if security_config['enable_access_log']:
            logging.warning(f"🚫 未授权的Web访问尝试: {client_ip}")
        return False
        
    except ValueError:
        if security_config['enable_access_log']:
            logging.warning(f"🚫 无效的客户端IP地址: {client_ip}")
        return False

def require_web_access():
    """Web访问权限装饰器"""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not check_web_access_permission():
                abort(403)  # Forbidden
            return f(*args, **kwargs)
        return decorated_function
    return decorator

def safe_import_getip():
    """安全导入 getip 模块"""
    try:
        getip_path = os.path.join(current_dir, 'modules', 'getip.py')
        if not os.path.exists(getip_path):
            logging.error(f"getip.py 文件不存在: {getip_path}")
            return None
        
        # 动态导入 modules.modules（如果存在）
        modules_path = os.path.join(current_dir, 'modules', 'modules.py')
        if os.path.exists(modules_path):
            modules_spec = importlib.util.spec_from_file_location("modules", modules_path)
            modules_module = importlib.util.module_from_spec(modules_spec)
            sys.modules['modules.modules'] = modules_module
            modules_spec.loader.exec_module(modules_module)
        
        # 动态导入 getip
        spec = importlib.util.spec_from_file_location("getip", getip_path)
        getip_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(getip_module)
        
        logging.info("✅ getip 模块加载成功")
        return getip_module.newip
        
    except Exception as e:
        logging.error(f"❌ getip 模块加载失败: {e}")
        return None

def run_in_executor(func, *args):
    """在线程池中运行同步函数"""
    return executor.submit(func, *args)

def schedule_coroutine(coro):
    """在主事件循环中调度协程"""
    if main_loop and main_loop.is_running():
        future = asyncio.run_coroutine_threadsafe(coro, main_loop)
        return future
    else:
        logging.error("❌ 主事件循环不可用")
        return None

class CountryMonitor:
    """自动国家检测和代理切换系统 - 修复版"""
    
    def __init__(self, target_country='US', check_interval=60, config=None):
        self.target_country = target_country
        self.check_interval = check_interval
        self.is_monitoring = False
        self.last_check_time = 0
        self.last_country = None
        self.consecutive_failures = 0
        self.max_failures = 3
        self.monitor_task = None
        
        # 黑名单功能支持
        self.config = config or {}
        self.enable_blacklist = self.config.get('enable_ip_blacklist', 'True').lower() == 'true'
        self.blacklist_url = self.config.get('ip_blacklist_url', '')
        
        # 初始化黑名单管理器
        if self.enable_blacklist and self.blacklist_url:
            try:
                from modules.country_proxy_manager import CountryBasedProxyManager
                self.proxy_manager = CountryBasedProxyManager(config or {})
                logging.info("✅ 黑名单管理器初始化成功")
            except Exception as e:
                logging.error(f"❌ 黑名单管理器初始化失败: {e}")
                self.proxy_manager = None
        else:
            self.proxy_manager = None
            if not self.enable_blacklist:
                logging.info("🚫 IP黑名单功能已禁用")
            elif not self.blacklist_url:
                logging.warning("⚠️ 未配置黑名单URL，黑名单功能不可用")
        
    async def start_monitoring(self):
        """启动自动监控"""
        if self.is_monitoring:
            logging.warning("⚠️ 国家监控已在运行中")
            return
            
        self.is_monitoring = True
        logging.info(f"🌍 启动自动国家监控 - 目标国家: {self.target_country}, 检测间隔: {self.check_interval}秒")
        
        # 创建监控任务
        self.monitor_task = asyncio.create_task(self._monitoring_loop())
        
    async def _monitoring_loop(self):
        """监控循环 - 增强版，包含黑名单定时更新"""
        # 🔧 修复：从配置中读取黑名单检查间隔，而不是硬编码
        blacklist_check_interval = int(self.config.get('blacklist_check_interval', '300'))  # 默认5分钟
        last_blacklist_check = 0
        
        while self.is_monitoring:
            try:
                current_time = time.time()
                
                # 🔧 新增：定时检查黑名单是否需要更新
                if current_time - last_blacklist_check >= blacklist_check_interval:
                    if self.proxy_manager and self.enable_blacklist:
                        try:
                            if self.proxy_manager._should_update_blacklist():
                                logging.info("⏰ 监控循环检测到黑名单需要更新...")
                                
                                # 在线程池中执行同步更新
                                loop = asyncio.get_event_loop()
                                success = await loop.run_in_executor(
                                    executor, 
                                    self.proxy_manager._sync_download_blacklist
                                )
                                
                                if success:
                                    logging.info("✅ 监控循环中黑名单更新成功")
                                else:
                                    logging.warning("⚠️ 监控循环中黑名单更新失败")
                            else:
                                # 每小时记录一次状态
                                hours_since_update = (current_time - self.proxy_manager.blacklist_last_update) / 3600 if self.proxy_manager.blacklist_last_update > 0 else 0
                                if hours_since_update > 0 and int(hours_since_update) % 6 == 0:  # 每6小时记录一次
                                    logging.debug(f"🛡️ 黑名单状态：距离上次更新 {hours_since_update:.1f} 小时，暂不需要更新")
                        except Exception as e:
                            logging.error(f"❌ 监控循环中黑名单检查异常: {e}")
                    
                    last_blacklist_check = current_time
                
                # 原有的代理国家检查
                await self.check_and_switch_if_needed()
                
                await asyncio.sleep(self.check_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logging.error(f"❌ 监控循环异常: {e}")
                await asyncio.sleep(10)
    
    def stop_monitoring(self):
        """停止监控"""
        self.is_monitoring = False
        if self.monitor_task:
            self.monitor_task.cancel()
        logging.info("🛑 自动国家监控已停止")
    
    async def check_and_switch_if_needed(self):
        """检查当前代理国家，必要时切换"""
        global current_proxy, proxy_stats
        
        if not current_proxy:
            logging.info("🔄 当前无代理，尝试获取新代理...")
            await self.switch_proxy("无代理")
            return
        
        try:
            # 使用原版的可靠检测方法（包含黑名单检查）
            country = await self.detect_proxy_country(current_proxy)
            
            if country:
                self.consecutive_failures = 0
                proxy_stats['current_country'] = country
                proxy_stats['total_checks'] += 1
                self.last_check_time = time.time()
                
                if self.last_country != country:
                    if self.last_country is not None:
                        proxy_stats['country_changes'] += 1
                        logging.info(f"🌍 检测到国家变化: {self.last_country} -> {country}")
                    else:
                        logging.info(f"🌍 首次检测到代理国家: {country}")
                    
                    self.last_country = country
                
                # 检查是否是黑名单IP
                if country == 'BLACKLISTED':
                    logging.warning("🚫 当前代理IP在黑名单中")
                    proxy_stats['blacklist_hits'] += 1
                    await self.switch_proxy("IP在黑名单中")
                elif country != self.target_country:
                    logging.warning(f"⚠️ 当前国家 {country} 不符合目标国家 {self.target_country}")
                    await self.switch_proxy(f"国家不匹配 ({country} != {self.target_country})")
                else:
                    logging.info(f"✅ 代理国家检查通过: {country}")
            
            else:
                self.consecutive_failures += 1
                logging.error(f"❌ 代理国家检测失败 (连续失败 {self.consecutive_failures}/{self.max_failures})")
                
                if self.consecutive_failures >= self.max_failures:
                    logging.error("❌ 连续检测失败次数过多，切换代理")
                    await self.switch_proxy("连续检测失败")
                    self.consecutive_failures = 0
        
        except Exception as e:
            logging.error(f"❌ 国家检测过程异常: {e}")
            self.consecutive_failures += 1
    
    async def detect_proxy_country(self, proxy_url):
        """检测代理的真实出口国家（集成黑名单检查）"""
        try:
            proxy_for_curl = proxy_url
            if proxy_for_curl.startswith('socks5://'):
                proxy_for_curl = proxy_for_curl[9:]
            
            cmd = [
                'curl', '-s', '--connect-timeout', '10', '--max-time', '15',
                '-x', f'socks5://{proxy_for_curl}',
                'https://ipinfo.io?token=68cdce81ca2b21'
            ]
            
            # 在线程池中运行subprocess
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                executor, 
                lambda: subprocess.run(cmd, capture_output=True, text=True, timeout=20)
            )
            
            if result.returncode == 0:
                try:
                    data = json.loads(result.stdout)
                    country = data.get('country')
                    ip = data.get('ip')
                    
                    if country and ip:
                        # 检查IP是否在黑名单中（如果黑名单管理器可用）
                        if self.proxy_manager and self.proxy_manager.is_ip_blacklisted(ip):
                            logging.warning(f"🚫 落地IP {ip} 在黑名单中，国家: {country}")
                            proxy_stats['blacklist_hits'] += 1
                            return 'BLACKLISTED'
                        
                        logging.debug(f"🌐 检测到代理信息: IP={ip}, 国家={country}")
                        return country
                    else:
                        logging.warning("⚠️ IP检测响应缺少必要字段")
                        return None
                        
                except json.JSONDecodeError:
                    logging.error("❌ IP检测服务返回无效JSON")
                    return None
            else:
                error_msg = result.stderr.strip() if result.stderr else "未知错误"
                logging.error(f"❌ curl 检测失败: {error_msg}")
                return None
                
        except Exception as e:
            logging.error(f"❌ 代理国家检测异常: {e}")
            return None
    
    async def switch_proxy(self, reason):
        """切换到新代理"""
        global current_proxy, proxy_stats
        
        logging.info(f"🔄 开始切换代理，原因: {reason}")
        
        try:
            newip_func = safe_import_getip()
            if not newip_func:
                logging.error("❌ getip 模块不可用，无法切换代理")
                return False
            
            max_attempts = 2
            for attempt in range(max_attempts):
                try:
                    # 在线程池中获取新代理
                    loop = asyncio.get_event_loop()
                    new_proxy = await loop.run_in_executor(executor, newip_func)
                    
                    if new_proxy:
                        logging.info(f"🧪 验证新代理国家 (尝试 {attempt + 1}/{max_attempts})")
                        # 使用统一的检测方法
                        country = await self.detect_proxy_country(new_proxy)
                        
                        if country == self.target_country:
                            old_proxy = current_proxy
                            current_proxy = new_proxy
                            proxy_stats['current_proxy'] = new_proxy
                            proxy_stats['current_country'] = country
                            proxy_stats['proxy_switches'] += 1
                            self.last_country = country
                            
                            logging.info(f"✅ 代理切换成功: {country} ({new_proxy.split('@')[-1] if '@' in new_proxy else new_proxy})")
                            return True
                        
                        elif country == 'BLACKLISTED':
                            logging.warning(f"⚠️ 新代理IP在黑名单中，重试...")
                        elif country:
                            logging.warning(f"⚠️ 新代理国家 {country} 不符合目标 {self.target_country}，重试...")
                        else:
                            logging.warning("⚠️ 新代理国家检测失败，重试...")
                    
                    else:
                        logging.error("❌ 获取新代理返回空值")
                    
                    if attempt < max_attempts - 1:
                        await asyncio.sleep(2)
                        
                except Exception as e:
                    logging.error(f"❌ 获取新代理失败 (尝试 {attempt + 1}): {e}")
                    if attempt < max_attempts - 1:
                        await asyncio.sleep(2)
            
            logging.error(f"❌ 在 {max_attempts} 次尝试后仍无法获取符合要求的代理")
            return False
            
        except Exception as e:
            logging.error(f"❌ 代理切换过程异常: {e}")
            return False
    
    def get_stats(self):
        """获取监控统计信息"""
        base_stats = {
            'is_monitoring': self.is_monitoring,
            'target_country': self.target_country,
            'check_interval': self.check_interval,
            'last_check_time': datetime.fromtimestamp(self.last_check_time).isoformat() if self.last_check_time else None,
            'last_country': self.last_country,
            'consecutive_failures': self.consecutive_failures
        }
        
        # 添加黑名单统计
        if self.proxy_manager:
            blacklist_stats = self.proxy_manager.get_blacklist_stats()
            base_stats.update({
                'blacklist_enabled': blacklist_stats['enabled'],
                'blacklist_loaded': blacklist_stats['loaded'],
                'blacklist_size': blacklist_stats['size'],
                'blacklist_source': blacklist_stats['source'],
                'blacklist_needs_update': blacklist_stats['needs_update']
            })
        else:
            base_stats.update({
                'blacklist_enabled': self.enable_blacklist,
                'blacklist_loaded': False,
                'blacklist_size': 0,
                'blacklist_source': 'disabled' if not self.enable_blacklist else 'not_configured',
                'blacklist_needs_update': False
            })
        
        return base_stats
    
    def get_blacklist_stats(self):
        """获取黑名单详细统计信息"""
        if self.proxy_manager:
            return self.proxy_manager.get_blacklist_stats()
        else:
            return {
                'enabled': self.enable_blacklist,
                'loaded': False,
                'size': 0,
                'source': 'disabled' if not self.enable_blacklist else 'not_configured',
                'needs_update': False,
                'last_update': None,
                'cache_file_exists': False,
                'meta_file_exists': False,
                'hours_since_update': 0,
                'update_interval_hours': 0,
                'url': self.blacklist_url,
                'meta_info': {}
            }
    
    def force_update_blacklist(self):
        """强制更新黑名单"""
        if self.proxy_manager:
            return self.proxy_manager.force_update_blacklist()
        else:
            logging.warning("⚠️ 黑名单管理器不可用")
            return None

class SOCKS5Server:
    """完整的 SOCKS5 代理服务器 - 增强安全版本（支持认证）"""
    
    def __init__(self, host='0.0.0.0', port=1080, username=None, password=None):
        self.host = host
        self.port = port
        self.running = False
        self.server = None
        # 🔐 新增SOCKS5认证支持
        self.username = username
        self.password = password
        self.auth_required = bool(username and password)
        
        if self.auth_required:
            logging.info(f"🔐 SOCKS5服务器启用认证模式: 用户名={self.username}")
        else:
            logging.info("🔓 SOCKS5服务器使用无认证模式")
        
    async def start(self):
        """启动 SOCKS5 服务器"""
        try:
            self.running = True
            self.server = await asyncio.start_server(
                self.handle_client, self.host, self.port
            )
            
            auth_info = f" (认证: {self.username})" if self.auth_required else " (无认证)"
            logging.info(f"🚀 SOCKS5 服务器已启动: {self.host}:{self.port}{auth_info}")
            
            async with self.server:
                await self.server.serve_forever()
                
        except Exception as e:
            logging.error(f"❌ SOCKS5 服务器启动失败: {e}")
            self.running = False
    
    async def stop(self):
        """停止 SOCKS5 服务器"""
        self.running = False
        if self.server:
            self.server.close()
            await self.server.wait_closed()
            logging.info("🛑 SOCKS5 服务器已停止")
    
    async def handle_client(self, reader, writer):
        """处理客户端连接"""
        client_addr = writer.get_extra_info('peername')
        
        # 记录连接（如果启用访问日志）
        if security_config['enable_access_log']:
            logging.info(f"📱 新SOCKS5客户端连接: {client_addr}")
        
        proxy_stats['connections_count'] += 1
        
        try:
            # SOCKS5 握手
            auth_method = await self.socks5_handshake(reader, writer)
            if auth_method is None:
                return
            
            # 如果需要认证
            if auth_method == 0x02:
                if not await self.socks5_authenticate(reader, writer):
                    return
            
            # 处理连接请求
            target_host, target_port = await self.socks5_connect_request(reader, writer)
            if not target_host:
                return
            
            await self.proxy_connection(target_host, target_port, reader, writer)
            
        except Exception as e:
            if security_config['enable_access_log']:
                logging.error(f"❌ 处理SOCKS5客户端连接失败 {client_addr}: {e}")
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except:
                pass
            if security_config['enable_access_log']:
                logging.debug(f"🔌 SOCKS5客户端连接关闭: {client_addr}")
    
    async def socks5_handshake(self, reader, writer):
        """SOCKS5 握手"""
        try:
            data = await asyncio.wait_for(reader.read(1024), timeout=10)
            
            if len(data) < 3 or data[0] != 0x05:
                logging.warning("❌ 无效的 SOCKS5 握手")
                return None
            
            num_methods = data[1]
            methods = list(data[2:2+num_methods])
            
            # 🔐 根据服务器配置选择认证方法
            if self.auth_required:
                if 0x02 in methods:  # 用户名/密码认证
                    writer.write(b'\x05\x02')
                    await writer.drain()
                    return 0x02
                else:
                    # 客户端不支持用户名密码认证
                    writer.write(b'\x05\xFF')  # 无可接受的认证方法
                    await writer.drain()
                    return None
            else:
                if 0x00 in methods:  # 无需认证
                    writer.write(b'\x05\x00')
                    await writer.drain()
                    return 0x00
                else:
                    writer.write(b'\x05\xFF')
                    await writer.drain()
                    return None
            
        except Exception as e:
            logging.error(f"❌ SOCKS5 握手失败: {e}")
            return None
    
    async def socks5_authenticate(self, reader, writer):
        """SOCKS5 用户名密码认证"""
        try:
            data = await asyncio.wait_for(reader.read(1024), timeout=10)
            
            if len(data) < 3 or data[0] != 0x01:
                logging.warning("❌ 无效的 SOCKS5 认证请求")
                writer.write(b'\x01\x01')  # 认证失败
                await writer.drain()
                return False
            
            username_len = data[1]
            username = data[2:2+username_len].decode('utf-8')
            password_len = data[2+username_len]
            password = data[3+username_len:3+username_len+password_len].decode('utf-8')
            
            # 🔐 验证用户名密码
            if username == self.username and password == self.password:
                writer.write(b'\x01\x00')  # 认证成功
                await writer.drain()
                if security_config['enable_access_log']:
                    logging.info(f"✅ SOCKS5认证成功: 用户={username}")
                return True
            else:
                writer.write(b'\x01\x01')  # 认证失败
                await writer.drain()
                if security_config['enable_access_log']:
                    logging.warning(f"🚫 SOCKS5认证失败: 用户={username}")
                return False
            
        except Exception as e:
            logging.error(f"❌ SOCKS5 认证失败: {e}")
            writer.write(b'\x01\x01')
            await writer.drain()
            return False
    
    async def socks5_connect_request(self, reader, writer):
        """处理 SOCKS5 连接请求"""
        try:
            data = await asyncio.wait_for(reader.read(1024), timeout=10)
            
            if len(data) < 10 or data[0] != 0x05:
                logging.warning("❌ 无效的 SOCKS5 连接请求")
                return None, None
            
            cmd = data[1]
            atyp = data[3]
            
            if cmd != 0x01:
                writer.write(b'\x05\x07\x00\x01\x00\x00\x00\x00\x00\x00')
                await writer.drain()
                return None, None
            
            if atyp == 0x01:  # IPv4
                target_host = socket.inet_ntoa(data[4:8])
                target_port = struct.unpack('>H', data[8:10])[0]
            elif atyp == 0x03:  # 域名
                addr_len = data[4]
                target_host = data[5:5+addr_len].decode('utf-8')
                target_port = struct.unpack('>H', data[5+addr_len:7+addr_len])[0]
            else:
                logging.warning(f"❌ 不支持的地址类型: {atyp}")
                writer.write(b'\x05\x08\x00\x01\x00\x00\x00\x00\x00\x00')
                await writer.drain()
                return None, None
            
            if security_config['enable_access_log']:
                logging.info(f"🎯 SOCKS5连接目标: {target_host}:{target_port}")
            return target_host, target_port
            
        except Exception as e:
            logging.error(f"❌ 解析SOCKS5连接请求失败: {e}")
            return None, None
    
    async def proxy_connection(self, target_host, target_port, client_reader, client_writer):
        """通过上游代理连接目标"""
        proxy_url = self.get_current_proxy()
        
        if not proxy_url:
            logging.error("❌ 没有可用的代理")
            client_writer.write(b'\x05\x01\x00\x01\x00\x00\x00\x00\x00\x00')
            await client_writer.drain()
            return
        
        try:
            proxy_info = self.parse_proxy_url(proxy_url)
            if not proxy_info:
                raise Exception("无效的代理URL")
            
            proxy_reader, proxy_writer = await asyncio.wait_for(
                asyncio.open_connection(proxy_info['host'], proxy_info['port']),
                timeout=10
            )
            
            success = await self.upstream_socks5_handshake(
                proxy_reader, proxy_writer, proxy_info
            )
            
            if not success:
                raise Exception("上游代理握手失败")
            
            success = await self.upstream_connect_request(
                proxy_reader, proxy_writer, target_host, target_port
            )
            
            if not success:
                raise Exception("上游代理连接目标失败")
            
            client_writer.write(b'\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00')
            await client_writer.drain()
            
            if security_config['enable_access_log']:
                logging.info(f"✅ SOCKS5代理连接建立: {target_host}:{target_port}")
            
            await asyncio.gather(
                self.pipe_data(client_reader, proxy_writer, "客户端->代理"),
                self.pipe_data(proxy_reader, client_writer, "代理->客户端"),
                return_exceptions=True
            )
            
        except Exception as e:
            logging.error(f"❌ SOCKS5代理连接失败: {e}")
            try:
                client_writer.write(b'\x05\x01\x00\x01\x00\x00\x00\x00\x00\x00')
                await client_writer.drain()
            except:
                pass
    
    def parse_proxy_url(self, proxy_url):
        """解析代理URL"""
        try:
            if '://' in proxy_url:
                proxy_url = proxy_url.split('://', 1)[1]
            
            if '@' in proxy_url:
                auth_part, addr_part = proxy_url.split('@', 1)
                username, password = auth_part.split(':', 1)
                host, port = addr_part.split(':', 1)
            else:
                username = password = None
                host, port = proxy_url.split(':', 1)
            
            return {
                'host': host,
                'port': int(port),
                'username': username,
                'password': password
            }
            
        except Exception as e:
            logging.error(f"❌ 解析代理URL失败: {e}")
            return None
    
    async def upstream_socks5_handshake(self, reader, writer, proxy_info):
        """与上游代理进行 SOCKS5 握手"""
        try:
            if proxy_info['username'] and proxy_info['password']:
                writer.write(b'\x05\x02\x00\x02')
            else:
                writer.write(b'\x05\x01\x00')
            
            await writer.drain()
            
            response = await asyncio.wait_for(reader.read(2), timeout=10)
            
            if len(response) != 2 or response[0] != 0x05:
                return False
            
            if response[1] == 0x02:
                if not proxy_info['username'] or not proxy_info['password']:
                    return False
                
                username = proxy_info['username'].encode('utf-8')
                password = proxy_info['password'].encode('utf-8')
                auth_data = struct.pack('B', len(username)) + username + struct.pack('B', len(password)) + password
                writer.write(b'\x01' + auth_data)
                await writer.drain()
                
                auth_response = await asyncio.wait_for(reader.read(2), timeout=10)
                if auth_response != b'\x01\x00':
                    return False
            
            elif response[1] != 0x00:
                return False
            
            return True
            
        except Exception as e:
            logging.error(f"❌ 上游代理握手失败: {e}")
            return False
    
    async def upstream_connect_request(self, reader, writer, target_host, target_port):
        """请求上游代理连接目标"""
        try:
            if target_host.replace('.', '').isdigit():
                addr_data = b'\x01' + socket.inet_aton(target_host)
            else:
                target_host_bytes = target_host.encode('utf-8')
                addr_data = b'\x03' + struct.pack('B', len(target_host_bytes)) + target_host_bytes
            
            request_data = b'\x05\x01\x00' + addr_data + struct.pack('>H', target_port)
            writer.write(request_data)
            await writer.drain()
            
            response = await asyncio.wait_for(reader.read(1024), timeout=15)
            
            if len(response) < 2 or response[0] != 0x05 or response[1] != 0x00:
                logging.error(f"❌ 上游代理连接失败，响应码: {response[1] if len(response) > 1 else 'unknown'}")
                return False
            
            return True
            
        except Exception as e:
            logging.error(f"❌ 上游代理连接请求失败: {e}")
            return False
    
    async def pipe_data(self, reader, writer, direction):
        """数据转发"""
        try:
            while True:
                data = await reader.read(8192)
                if not data:
                    break
                
                writer.write(data)
                await writer.drain()
                
                proxy_stats['bytes_transferred'] += len(data)
                
        except Exception as e:
            logging.debug(f"🔄 SOCKS5数据转发结束 ({direction}): {e}")
        finally:
            try:
                writer.close()
            except:
                pass
    
    def get_current_proxy(self):
        """获取当前代理"""
        global current_proxy
        
        if not current_proxy:
            logging.info("🔄 当前无代理，尝试自动获取...")
            try:
                newip_func = safe_import_getip()
                if newip_func:
                    current_proxy = newip_func()
                    if current_proxy:
                        proxy_stats['current_proxy'] = current_proxy
                        proxy_stats['proxy_switches'] += 1
                        logging.info(f"✅ 自动获取代理成功: {current_proxy}")
                    else:
                        logging.error("❌ 获取代理返回空值")
                else:
                    logging.error("❌ getip 模块不可用")
            except Exception as e:
                logging.error(f"❌ 自动获取代理失败: {e}")
        
        return current_proxy

def init_country_monitor():
    """初始化国家监控"""
    global country_monitor
    config = load_simple_config()
    target_country = config.get('target_country', 'US')
    check_interval = int(config.get('country_check_interval', '60'))
    
    # 传递完整配置给CountryMonitor，启用黑名单功能
    country_monitor = CountryMonitor(target_country, check_interval, config)
    return country_monitor


 # HTML 模板（增强版 - 包含黑名单功能但隐藏安全设置）
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>ProxyCat - 智能代理管理</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 20px;
        }
        .container {
            max-width: 1200px;
            margin: 0 auto;
            background: rgba(255, 255, 255, 0.95);
            border-radius: 15px;
            box-shadow: 0 20px 40px rgba(0, 0, 0, 0.1);
            overflow: hidden;
        }
        .header {
            background: linear-gradient(45deg, #2c3e50, #3498db);
            color: white;
            padding: 30px;
            text-align: center;
        }
        .header h1 { font-size: 2.5em; margin-bottom: 10px; }
        .status-banner {
            background: #27ae60;
            color: white;
            padding: 15px;
            text-align: center;
            font-weight: bold;
        }
        .monitoring-status {
            padding: 12px;
            text-align: center;
            font-weight: 500;
            background: #3498db;
            color: white;
        }
        .monitoring-status.active { background: #27ae60; }
        .monitoring-status.inactive { background: #e74c3c; }
        .main-content { padding: 30px; }
        .stats-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
            gap: 15px;
            margin-bottom: 25px;
        }
        .stat-card {
            background: white;
            border-radius: 10px;
            padding: 15px;
            box-shadow: 0 5px 15px rgba(0, 0, 0, 0.08);
            border-left: 4px solid #3498db;
            text-align: center;
            transition: transform 0.2s;
        }
        .stat-card:hover { transform: translateY(-2px); }
        .stat-card.blacklist { border-left-color: #e74c3c; }
        .stat-value {
            font-size: 1.4em;
            font-weight: bold;
            margin-bottom: 5px;
            color: #2c3e50;
        }
        .stat-label { color: #666; font-size: 0.85em; }
        .control-panel {
            background: white;
            border-radius: 10px;
            padding: 25px;
            margin-bottom: 20px;
            box-shadow: 0 5px 15px rgba(0, 0, 0, 0.08);
        }
        .control-panel h3 {
            margin-bottom: 20px;
            color: #2c3e50;
            border-bottom: 2px solid #ecf0f1;
            padding-bottom: 10px;
        }
        .btn {
            background: #3498db;
            color: white;
            border: none;
            padding: 10px 20px;
            border-radius: 6px;
            cursor: pointer;
            font-size: 13px;
            margin: 3px;
            transition: all 0.3s;
            font-weight: 500;
        }
        .btn:hover { background: #2980b9; transform: translateY(-1px); }
        .btn.success { background: #27ae60; }
        .btn.success:hover { background: #229954; }
        .btn.warning { background: #f39c12; }
        .btn.warning:hover { background: #e67e22; }
        .btn.danger { background: #e74c3c; }
        .btn.danger:hover { background: #c0392b; }
        .btn:disabled { background: #95a5a6; cursor: not-allowed; transform: none; }
        .input-field {
            padding: 8px 12px;
            border: 2px solid #ecf0f1;
            border-radius: 6px;
            font-size: 13px;
            margin: 3px;
            transition: border-color 0.3s;
        }
        .input-field:focus { border-color: #3498db; outline: none; }
        .proxy-info {
            background: #f8f9fa;
            border: 1px solid #dee2e6;
            border-radius: 8px;
            padding: 12px;
            margin: 10px 0;
            font-family: 'Courier New', monospace;
            font-size: 12px;
            word-break: break-all;
        }
        .monitor-info {
            background: #e3f2fd;
            border: 1px solid #90caf9;
            border-radius: 8px;
            padding: 12px;
            margin: 10px 0;
            font-size: 13px;
            line-height: 1.4;
        }
        .blacklist-info {
            background: #fff3e0;
            border: 1px solid #ffb74d;
            border-radius: 8px;
            padding: 12px;
            margin: 10px 0;
            font-size: 13px;
            line-height: 1.4;
        }
        .security-notice {
            background: #e8f5e8;
            border: 1px solid #4caf50;
            border-radius: 8px;
            padding: 12px;
            margin: 10px 0;
            font-size: 13px;
            line-height: 1.4;
            color: #2e7d32;
        }
        .alert {
            padding: 15px;
            border-radius: 5px;
            position: fixed;
            top: 20px;
            right: 20px;
            z-index: 9999;
            min-width: 300px;
            animation: slideIn 0.3s ease;
        }
        @keyframes slideIn {
            from { transform: translateX(100%); opacity: 0; }
            to { transform: translateX(0); opacity: 1; }
        }
        .alert.success { background: #d4edda; color: #155724; border: 1px solid #c3e6cb; }
        .alert.error { background: #f8d7da; color: #721c24; border: 1px solid #f5c6cb; }
        .alert.warning { background: #fff3cd; color: #856404; border: 1px solid #ffeaa7; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🐱 ProxyCat</h1>
            <p>智能代理池管理系统 - 增强安全版</p>
        </div>
        
        <div class="status-banner">
            🚀 SOCKS5 代理服务器运行中 - localhost:1080 (已启用认证保护)
        </div>
        
        <div class="monitoring-status" id="monitoring-status">
            🔍 监控状态检查中...
        </div>
        
        <div class="main-content">
            <div class="security-notice">
                🔒 <strong>安全提示:</strong> 当前服务已启用增强安全功能，包括SOCKS5认证和Web面板访问控制。
            </div>
            
            <div class="stats-grid">
                <div class="stat-card">
                    <div class="stat-value" id="connections-count">0</div>
                    <div class="stat-label">总连接数</div>
                </div>
                <div class="stat-card">
                    <div class="stat-value" id="proxy-switches">0</div>
                    <div class="stat-label">代理切换</div>
                </div>
                <div class="stat-card">
                    <div class="stat-value" id="total-checks">0</div>
                    <div class="stat-label">国家检测</div>
                </div>
                <div class="stat-card">
                    <div class="stat-value" id="country-changes">0</div>
                    <div class="stat-label">国家变化</div>
                </div>
                <div class="stat-card">
                    <div class="stat-value" id="current-country">-</div>
                    <div class="stat-label">当前国家</div>
                </div>
                <div class="stat-card blacklist">
                    <div class="stat-value" id="blacklist-status">-</div>
                    <div class="stat-label">黑名单状态</div>
                </div>
                <div class="stat-card blacklist">
                    <div class="stat-value" id="blacklist-size">0</div>
                    <div class="stat-label">黑名单大小</div>
                </div>
                <div class="stat-card blacklist">
                    <div class="stat-value" id="blacklist-hits">0</div>
                    <div class="stat-label">黑名单拦截</div>
                </div>
                <div class="stat-card">
                    <div class="stat-value" id="bytes-transferred">0 B</div>
                    <div class="stat-label">数据传输</div>
                </div>
            </div>
            
            <div class="control-panel">
                <h3>🤖 自动国家监控</h3>
                
                <div class="monitor-info" id="monitor-info">
                    监控信息加载中...
                </div>
                
                <div style="margin-bottom: 15px;">
                    <label>目标国家:</label>
                    <input type="text" id="target-country-input" class="input-field" value="US" maxlength="2" style="width: 80px;">
                    <button class="btn" onclick="updateTargetCountry()">🎯 更新</button>
                </div>
                
                <div>
                    <button class="btn success" id="start-monitor-btn" onclick="startMonitoring()">▶️ 启动监控</button>
                    <button class="btn danger" id="stop-monitor-btn" onclick="stopMonitoring()">⏹️ 停止监控</button>
                    <button class="btn" onclick="checkMonitorStatus()">📋 检查状态</button>
                </div>
            </div>
            
            <div class="control-panel">
                <h3>🛡️ 黑名单管理</h3>
                
                <div class="blacklist-info" id="blacklist-info">
                    黑名单信息加载中...
                </div>
                
                <div>
                    <button class="btn" onclick="checkBlacklistStatus()">📋 检查状态</button>
                    <button class="btn warning" onclick="forceUpdateBlacklist()">🔄 强制更新</button>
                    <button class="btn" onclick="debugBlacklist()">🔍 调试信息</button>
                </div>
            </div>
            
            <div class="control-panel">
                <h3>🎮 手动控制</h3>
                
                <div class="proxy-info">
                    <strong>当前代理:</strong><br>
                    <span id="current-proxy-display">未设置</span>
                </div>
                
                <div>
                    <button class="btn warning" onclick="manualSwitchProxy()">🔄 手动切换</button>
                    <button class="btn" onclick="testProxy()">🧪 测试代理</button>
                    <button class="btn" onclick="refreshStats()">📊 刷新统计</button>
                </div>
            </div>
        </div>
    </div>
    
    <div id="alert-container"></div>

    <script>
        let statsRefreshInterval;
        
        document.addEventListener('DOMContentLoaded', function() {
            refreshStats();
            checkMonitorStatus();
            checkBlacklistStatus();
            startAutoRefresh();
        });
        
        function startAutoRefresh() {
            statsRefreshInterval = setInterval(() => {
                refreshStats();
                checkMonitorStatus();
            }, 3000);
        }
        
        async function refreshStats() {
            try {
                const response = await fetch('/api/proxy/stats');
                const data = await response.json();
                
                if (data.success) {
                    updateStatsDisplay(data.data);
                }
            } catch (error) {
                console.error('刷新统计失败:', error);
            }
        }
        
        async function checkMonitorStatus() {
            try {
                const response = await fetch('/api/monitor/status');
                const data = await response.json();
                
                if (data.success) {
                    updateMonitorStatus(data.data);
                }
            } catch (error) {
                console.error('检查监控状态失败:', error);
            }
        }
        
        async function checkBlacklistStatus() {
            try {
                const response = await fetch('/api/blacklist/status');
                const data = await response.json();
                
                if (data.success) {
                    updateBlacklistInfo(data.data);
                }
            } catch (error) {
                console.error('检查黑名单状态失败:', error);
            }
        }
        
        async function debugBlacklist() {
            try {
                const response = await fetch('/api/blacklist/debug');
                const data = await response.json();
                
                if (data.success) {
                    const debugInfo = JSON.stringify(data.data, null, 2);
                    alert('黑名单调试信息:\\n' + debugInfo);
                } else {
                    showAlert('获取调试信息失败: ' + data.error, 'error');
                }
            } catch (error) {
                showAlert('调试信息请求失败: ' + error.message, 'error');
            }
        }
        
        function updateStatsDisplay(stats) {
            document.getElementById('connections-count').textContent = stats.connections_count || 0;
            document.getElementById('proxy-switches').textContent = stats.proxy_switches || 0;
            document.getElementById('total-checks').textContent = stats.total_checks || 0;
            document.getElementById('country-changes').textContent = stats.country_changes || 0;
            document.getElementById('current-country').textContent = stats.current_country || '-';
            document.getElementById('blacklist-hits').textContent = stats.blacklist_hits || 0;
            
            const bytes = stats.bytes_transferred || 0;
            let size = bytes < 1024 ? bytes + ' B' :
                      bytes < 1024*1024 ? (bytes/1024).toFixed(1) + ' KB' :
                      (bytes/1024/1024).toFixed(1) + ' MB';
            document.getElementById('bytes-transferred').textContent = size;
            
            // 更新黑名单状态
            const blacklistStatus = document.getElementById('blacklist-status');
            const blacklistSize = document.getElementById('blacklist-size');
            
            if (stats.blacklist_enabled) {
                if (stats.blacklist_loaded) {
                    blacklistStatus.textContent = '✅ 已加载';
                    blacklistStatus.style.color = '#27ae60';
                } else {
                    blacklistStatus.textContent = '❌ 失败';
                    blacklistStatus.style.color = '#e74c3c';
                }
                blacklistSize.textContent = stats.blacklist_size || 0;
            } else {
                blacklistStatus.textContent = '🚫 禁用';
                blacklistStatus.style.color = '#95a5a6';
                blacklistSize.textContent = '0';
            }
            
            // 更新代理显示
            const proxyDisplay = document.getElementById('current-proxy-display');
            if (stats.current_proxy) {
                const displayProxy = stats.current_proxy.includes('@') ? 
                    stats.current_proxy.split('@')[1] : stats.current_proxy;
                proxyDisplay.textContent = displayProxy;
                proxyDisplay.style.color = '#27ae60';
            } else {
                proxyDisplay.textContent = '未设置';
                proxyDisplay.style.color = '#e74c3c';
            }
            
            document.getElementById('target-country-input').value = stats.target_country || 'US';
        }
        
        function updateMonitorStatus(monitorData) {
            const statusEl = document.getElementById('monitoring-status');
            const infoEl = document.getElementById('monitor-info');
            const startBtn = document.getElementById('start-monitor-btn');
            const stopBtn = document.getElementById('stop-monitor-btn');
            
            if (monitorData.is_monitoring) {
                statusEl.textContent = '🤖 自动监控运行中';
                statusEl.className = 'monitoring-status active';
                startBtn.disabled = true;
                stopBtn.disabled = false;
            } else {
                statusEl.textContent = '😴 自动监控已停止';
                statusEl.className = 'monitoring-status inactive';
                startBtn.disabled = false;
                stopBtn.disabled = true;
            }
            
            let infoHtml = "<strong>目标国家:</strong> " + monitorData.target_country + "<br>" +
                          "<strong>检测间隔:</strong> " + monitorData.check_interval + "秒<br>" +
                          "<strong>上次检测:</strong> " + (monitorData.last_check_time ? new Date(monitorData.last_check_time).toLocaleString() : '未检测') + "<br>" +
                          "<strong>连续失败:</strong> " + monitorData.consecutive_failures + "次";
            infoEl.innerHTML = infoHtml;
        }
        
        function updateBlacklistInfo(blacklistData) {
            const infoEl = document.getElementById('blacklist-info');
            
            if (!blacklistData.enabled) {
                infoEl.innerHTML = '<strong>状态:</strong> 🚫 功能已禁用';
                return;
            }
            
            let statusText = blacklistData.loaded ? '✅ 已加载' : '❌ 未加载';
            let sourceText = {
                'local': '本地缓存',
                'remote': '远程下载', 
                'remote_sync': '远程同步',
                'remote_async': '远程异步',
                'empty': '空',
                'disabled': '禁用',
                'unknown': '未知'
            }[blacklistData.source] || blacklistData.source;
            
            let updateText = blacklistData.needs_update ? '⏰ 需要更新' : '✅ 最新';
            
            let infoHtml = "<strong>状态:</strong> " + statusText + "<br>" +
                          "<strong>大小:</strong> " + blacklistData.size + " 条记录<br>" +
                          "<strong>来源:</strong> " + sourceText + "<br>" +
                          "<strong>更新状态:</strong> " + updateText + "<br>" +
                          "<strong>上次更新:</strong> " + (blacklistData.last_update ? 
                              new Date(blacklistData.last_update).toLocaleString() : '从未更新') + "<br>" +
                          "<strong>缓存文件:</strong> " + (blacklistData.cache_file_exists ? '✅ 存在' : '❌ 不存在') + "<br>" +
                          "<strong>更新间隔:</strong> " + blacklistData.update_interval_hours + "小时";
            
            if (blacklistData.meta_info && blacklistData.meta_info.valid_count) {
                infoHtml += "<br><strong>有效记录:</strong> " + blacklistData.meta_info.valid_count;
                if (blacklistData.meta_info.invalid_count > 0) {
                    infoHtml += " (忽略 " + blacklistData.meta_info.invalid_count + " 条无效记录)";
                }
            }
            
            infoEl.innerHTML = infoHtml;
        }
        
        async function startMonitoring() {
            try {
                showAlert('正在启动自动监控...', 'warning');
                const response = await fetch('/api/monitor/start', { method: 'POST' });
                const data = await response.json();
                
                if (data.success) {
                    showAlert('自动监控已启动！', 'success');
                    checkMonitorStatus();
                } else {
                    showAlert('启动失败: ' + data.message, 'error');
                }
            } catch (error) {
                showAlert('启动失败: ' + error.message, 'error');
            }
        }
        
        async function stopMonitoring() {
            try {
                showAlert('正在停止自动监控...', 'warning');
                const response = await fetch('/api/monitor/stop', { method: 'POST' });
                const data = await response.json();
                
                if (data.success) {
                    showAlert('自动监控已停止！', 'success');
                    checkMonitorStatus();
                } else {
                    showAlert('停止失败: ' + data.message, 'error');
                }
            } catch (error) {
                showAlert('停止失败: ' + error.message, 'error');
            }
        }
        
        async function manualSwitchProxy() {
            try {
                showAlert('正在切换代理...', 'warning');
                const response = await fetch('/api/proxy/switch', { method: 'POST' });
                const data = await response.json();
                
                if (data.success) {
                    showAlert('代理切换成功！', 'success');
                    refreshStats();
                } else {
                    showAlert('代理切换失败: ' + data.error, 'error');
                }
            } catch (error) {
                showAlert('代理切换失败: ' + error.message, 'error');
            }
        }
        
        async function testProxy() {
            try {
                showAlert('正在测试代理...', 'warning');
                const response = await fetch('/api/proxy/test', { method: 'POST' });
                const data = await response.json();
                
                if (data.success) {
                    showAlert('代理测试成功！IP: ' + data.ip + ', 国家: ' + data.country, 'success');
                } else {
                    showAlert('代理测试失败: ' + data.error, 'error');
                }
            } catch (error) {
                showAlert('代理测试失败: ' + error.message, 'error');
            }
        }
        
        async function updateTargetCountry() {
            const countryInput = document.getElementById('target-country-input');
            const country = countryInput.value.trim().toUpperCase();
            
            if (country.length !== 2) {
                showAlert('请输入2位国家代码', 'error');
                return;
            }
            
            try {
                const response = await fetch('/api/proxy/country', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ country: country })
                });
                
                const data = await response.json();
                
                if (data.success) {
                    showAlert('目标国家更新成功！', 'success');
                    refreshStats();
                    checkMonitorStatus();
                } else {
                    showAlert('更新失败: ' + data.error, 'error');
                }
            } catch (error) {
                showAlert('更新失败: ' + error.message, 'error');
            }
        }
        
        async function forceUpdateBlacklist() {
            try {
                showAlert('正在强制更新黑名单...', 'warning');
                const response = await fetch('/api/blacklist/update', { method: 'POST' });
                const data = await response.json();
                
                if (data.success) {
                    showAlert('黑名单更新成功！', 'success');
                    setTimeout(() => {
                        checkBlacklistStatus();
                        refreshStats();
                    }, 2000);
                } else {
                    showAlert('更新失败: ' + data.error, 'error');
                }
            } catch (error) {
                showAlert('更新失败: ' + error.message, 'error');
            }
        }
        
        function showAlert(message, type) {
            const alertContainer = document.getElementById('alert-container');
            const alertDiv = document.createElement('div');
            alertDiv.className = 'alert ' + type;
            alertDiv.textContent = message;
            
            alertContainer.appendChild(alertDiv);
            
            setTimeout(() => {
                if (alertDiv.parentNode) {
                    alertDiv.parentNode.removeChild(alertDiv);
                }
            }, 4000);
        }
    </script>
</body>
</html>
"""

# ===== 增强安全的 Flask 路由 =====

# 🔒 受保护的主页路由（需要正确的访问后缀）
@app.route('/')
def index_redirect():
    """根路径重定向或返回404"""
    # 直接访问根路径返回404，增加安全性
    abort(404)

@app.route('/<path:suffix>')
@require_web_access()
def secure_index(suffix):
    """安全的主页访问"""
    # 检查访问后缀是否正确
    if f'/{suffix}' != security_config['web_access_suffix']:
        abort(404)
    
    # 记录访问日志
    if security_config['enable_access_log']:
        client_ip = request.environ.get('HTTP_X_FORWARDED_FOR', request.environ.get('REMOTE_ADDR', ''))
        logging.info(f"🌐 Web管理面板访问: {client_ip}")
    
    return render_template_string(HTML_TEMPLATE)

# API路由保持不变，但加上访问权限检查
@app.route('/api/proxy/stats')
@require_web_access()
def get_proxy_stats():
    """获取增强的统计信息"""
    try:
        stats = dict(proxy_stats)
        
        if country_monitor:
            monitor_stats = country_monitor.get_stats()
            stats.update(monitor_stats)
        else:
            stats.update({
                'is_monitoring': False,
                'last_check_time': None,
                'consecutive_failures': 0
            })
        
        return jsonify({
            'success': True,
            'data': stats
        })
    except Exception as e:
        logging.error(f"❌ 获取统计信息失败: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/proxy/test', methods=['POST'])
@require_web_access()
def test_proxy():
    """测试当前代理"""
    try:
        if not current_proxy:
            return jsonify({
                'success': False,
                'error': '当前没有设置代理'
            })
        
        proxy_for_curl = current_proxy
        if proxy_for_curl.startswith('socks5://'):
            proxy_for_curl = proxy_for_curl[9:]
        
        cmd = [
            'curl', '-s', '--connect-timeout', '10', '--max-time', '15',
            '-x', f'socks5://{proxy_for_curl}',
            'https://ipinfo.io?token=68cdce81ca2b21'
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        
        if result.returncode == 0:
            try:
                data = json.loads(result.stdout)
                ip = data.get('ip', 'Unknown')
                country = data.get('country', 'Unknown')
                
                proxy_stats['current_country'] = country
                
                logging.info(f"✅ 代理测试成功: IP={ip}, 国家={country}")
                return jsonify({
                    'success': True,
                    'ip': ip,
                    'country': country,
                    'full_info': data
                })
            except json.JSONDecodeError:
                return jsonify({
                    'success': False,
                    'error': 'IP检测服务返回无效数据'
                })
        else:
            error_msg = result.stderr or '代理连接失败'
            logging.error(f"❌ 代理测试失败: {error_msg}")
            return jsonify({
                'success': False,
                'error': error_msg
            })
            
    except subprocess.TimeoutExpired:
        return jsonify({
            'success': False,
            'error': '代理测试超时'
        })
    except Exception as e:
        logging.error(f"❌ 代理测试异常: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/proxy/country', methods=['GET', 'POST'])
@require_web_access()
def manage_target_country():
    global proxy_stats, country_monitor
    
    if request.method == 'GET':
        try:
            return jsonify({
                'success': True,
                'target_country': proxy_stats['target_country']
            })
        except Exception as e:
            return jsonify({
                'success': False,
                'error': str(e)
            }), 500
    
    elif request.method == 'POST':
        try:
            data = request.get_json()
            country = data.get('country', '').strip().upper()
            
            if len(country) != 2:
                return jsonify({
                    'success': False,
                    'error': '国家代码必须是2位字母'
                }), 400
            
            proxy_stats['target_country'] = country
            
            if country_monitor:
                country_monitor.target_country = country
                logging.info(f"🎯 监控器目标国家已更新为: {country}")
            
            logging.info(f"🎯 目标国家已设置为: {country}")
            
            return jsonify({
                'success': True,
                'message': f'目标国家已设置为: {country}'
            })
        except Exception as e:
            logging.error(f"❌ 设置目标国家失败: {e}")
            return jsonify({
                'success': False,
                'error': str(e)
            }), 500

@app.route('/api/monitor/start', methods=['POST'])
@require_web_access()
def start_country_monitoring():
    """启动自动国家监控"""
    global country_monitor, main_loop
    
    try:
        if not country_monitor:
            country_monitor = init_country_monitor()
        
        if country_monitor.is_monitoring:
            return jsonify({
                'success': False,
                'message': '监控已在运行中'
            })
        
        # 在主事件循环中启动监控
        if main_loop and main_loop.is_running():
            future = asyncio.run_coroutine_threadsafe(
                country_monitor.start_monitoring(), 
                main_loop
            )
            # 不等待完成，让它在后台运行
            
            return jsonify({
                'success': True,
                'message': '自动国家监控已启动',
                'target_country': country_monitor.target_country,
                'check_interval': country_monitor.check_interval
            })
        else:
            return jsonify({
                'success': False,
                'error': '主事件循环不可用'
            }), 500
        
    except Exception as e:
        logging.error(f"❌ 启动监控失败: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/monitor/stop', methods=['POST'])
@require_web_access()
def stop_country_monitoring():
    """停止自动国家监控"""
    global country_monitor
    
    try:
        if country_monitor and country_monitor.is_monitoring:
            country_monitor.stop_monitoring()
            return jsonify({
                'success': True,
                'message': '自动国家监控已停止'
            })
        else:
            return jsonify({
                'success': False,
                'message': '监控未在运行'
            })
            
    except Exception as e:
        logging.error(f"❌ 停止监控失败: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/monitor/status')
@require_web_access()
def get_monitor_status():
    """获取监控状态"""
    global country_monitor
    
    try:
        if country_monitor:
            monitor_stats = country_monitor.get_stats()
            return jsonify({
                'success': True,
                'data': monitor_stats
            })
        else:
            return jsonify({
                'success': True,
                'data': {
                    'is_monitoring': False,
                    'target_country': proxy_stats.get('target_country', 'US'),
                    'check_interval': 60,
                    'last_check_time': None,
                    'last_country': None,
                    'consecutive_failures': 0
                }
            })
            
    except Exception as e:
        logging.error(f"❌ 获取监控状态失败: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/blacklist/status')
@require_web_access()
def get_blacklist_status():
    """获取黑名单详细状态"""
    try:
        if country_monitor and hasattr(country_monitor, 'get_blacklist_stats'):
            blacklist_stats = country_monitor.get_blacklist_stats()
            return jsonify({
                'success': True,
                'data': blacklist_stats
            })
        else:
            return jsonify({
                'success': True,
                'data': {
                    'enabled': False,
                    'loaded': False,
                    'size': 0,
                    'source': 'not_available',
                    'needs_update': False
                }
            })
    except Exception as e:
        logging.error(f"❌ 获取黑名单状态失败: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/blacklist/update', methods=['POST'])
@require_web_access()
def force_update_blacklist():
    """强制更新黑名单"""
    try:
        if not country_monitor or not hasattr(country_monitor, 'proxy_manager'):
            return jsonify({
                'success': False,
                'error': '黑名单功能不可用'
            }), 400
        
        proxy_manager = country_monitor.proxy_manager
        if not proxy_manager:
            return jsonify({
                'success': False,
                'error': '黑名单管理器未初始化'
            }), 400
            
        if not getattr(proxy_manager, 'enable_blacklist', False):
            return jsonify({
                'success': False,
                'error': '黑名单功能已禁用'
            }), 400
        
        try:
            logging.info("🔄 Web界面触发黑名单强制更新...")
            
            # 记录更新前状态
            old_size = len(proxy_manager.ip_blacklist)
            old_update_time = proxy_manager.blacklist_last_update
            
            # 重置更新时间，强制更新
            proxy_manager.blacklist_last_update = 0
            
            # 执行同步下载
            success = proxy_manager._sync_download_blacklist()
            
            if success:
                new_size = len(proxy_manager.ip_blacklist)
                logging.info(f"✅ Web界面黑名单强制更新成功: {old_size} -> {new_size} 条记录")
                
                return jsonify({
                    'success': True,
                    'message': f'黑名单强制更新成功，从 {old_size} 更新到 {new_size} 条记录',
                    'old_size': old_size,
                    'new_size': new_size
                })
            else:
                # 恢复原时间
                proxy_manager.blacklist_last_update = old_update_time
                logging.error("❌ Web界面黑名单强制更新失败")
                
                return jsonify({
                    'success': False,
                    'error': '黑名单下载失败，请检查网络连接和URL配置'
                })
                
        except Exception as e:
            logging.error(f"❌ 黑名单强制更新异常: {e}")
            return jsonify({
                'success': False,
                'error': f'更新异常: {str(e)}'
            })
        
    except Exception as e:
        logging.error(f"❌ 强制更新黑名单路由失败: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/blacklist/debug')
@require_web_access()
def debug_blacklist():
    """调试黑名单状态 - 详细信息"""
    try:
        if not country_monitor or not country_monitor.proxy_manager:
            return jsonify({
                'success': False,
                'error': '黑名单管理器不可用'
            })
        
        manager = country_monitor.proxy_manager
        current_time = time.time()
        
        debug_info = {
            # 基本状态
            'enabled': manager.enable_blacklist,
            'loaded': manager.blacklist_loaded,
            'size': len(manager.ip_blacklist),
            'url': manager.blacklist_url,
            
            # 时间相关
            'update_interval_seconds': manager.blacklist_update_interval,
            'update_interval_hours': manager.blacklist_update_interval / 3600,
            'last_update_timestamp': manager.blacklist_last_update,
            'current_timestamp': current_time,
            'seconds_since_update': current_time - manager.blacklist_last_update if manager.blacklist_last_update > 0 else 0,
            'hours_since_update': (current_time - manager.blacklist_last_update) / 3600 if manager.blacklist_last_update > 0 else 0,
            
            # 更新逻辑
            'should_update': manager._should_update_blacklist(),
            'never_updated': manager.blacklist_last_update == 0,
            
            # 文件状态
            'cache_files': {
                'blacklist_exists': os.path.exists(manager.blacklist_cache_file),
                'meta_exists': os.path.exists(manager.blacklist_meta_file),
                'blacklist_path': manager.blacklist_cache_file,
                'meta_path': manager.blacklist_meta_file
            },
            
            # 监控状态
            'monitoring_enabled': country_monitor.is_monitoring if country_monitor else False
        }
        
        # 添加格式化时间
        if manager.blacklist_last_update > 0:
            debug_info['last_update_formatted'] = datetime.fromtimestamp(
                manager.blacklist_last_update
            ).strftime('%Y-%m-%d %H:%M:%S')
        else:
            debug_info['last_update_formatted'] = '从未更新'
        
        # 添加元数据信息
        try:
            meta_info = manager._load_blacklist_meta()
            debug_info['metadata'] = meta_info
        except Exception as e:
            debug_info['metadata_error'] = str(e)
        
        # 文件详细信息
        if os.path.exists(manager.blacklist_cache_file):
            try:
                stat = os.stat(manager.blacklist_cache_file)
                debug_info['cache_file_info'] = {
                    'size_bytes': stat.st_size,
                    'modified_time': datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M:%S'),
                    'age_hours': (current_time - stat.st_mtime) / 3600
                }
            except Exception as e:
                debug_info['cache_file_error'] = str(e)
        
        return jsonify({
            'success': True,
            'data': debug_info
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/proxy/switch', methods=['POST'])
@require_web_access()
def manual_switch():
    global current_proxy, proxy_stats
    
    try:
        newip_func = safe_import_getip()
        if not newip_func:
            return jsonify({
                'success': False,
                'error': 'getip 模块不可用，请检查配置'
            }), 500
        
        logging.info("🔄 手动切换代理...")
        new_proxy = newip_func()
        
        if new_proxy:
            old_proxy = current_proxy
            current_proxy = new_proxy
            proxy_stats['current_proxy'] = new_proxy
            proxy_stats['proxy_switches'] += 1
            
            logging.info(f"✅ 手动切换代理成功: {old_proxy} -> {new_proxy}")
            return jsonify({
                'success': True,
                'message': '代理切换成功',
                'old_proxy': old_proxy,
                'new_proxy': new_proxy
            })
        else:
            logging.error("❌ 获取新代理失败")
            return jsonify({
                'success': False,
                'error': '无法获取新代理'
            })
            
    except Exception as e:
        logging.error(f"❌ 手动切换代理失败: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

def run_flask_app(port=5000):
    """运行Flask应用"""
    try:
        logging.info(f"🌐 启动 Web 管理界面: http://0.0.0.0:{port}")
        logging.info(f"🔒 安全访问地址: http://localhost:{port}{security_config['web_access_suffix']}")
        app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)
    except Exception as e:
        logging.error(f"❌ Flask应用启动失败: {e}")

async def start_socks_server():
    """启动增强安全的 SOCKS5 服务器"""
    global socks_server
    try:
        config = load_simple_config()
        port = int(config.get('port', 1080))
        
        # 🔐 使用安全配置中的认证信息
        username = security_config['socks5_username']
        password = security_config['socks5_password']
        
        socks_server = SOCKS5Server('0.0.0.0', port, username, password)
        await socks_server.start()
    except Exception as e:
        logging.error(f"❌ SOCKS5 服务器启动失败: {e}")

def signal_handler(signum, frame):
    """信号处理器"""
    logging.info("🛑 接收到停止信号，正在关闭服务器...")
    
    if socks_server:
        try:
            if main_loop and main_loop.is_running():
                asyncio.run_coroutine_threadsafe(socks_server.stop(), main_loop)
        except:
            pass
    
    if country_monitor:
        country_monitor.stop_monitoring()
    
    sys.exit(0)

async def main():
    """主函数"""
    global proxy_stats, country_monitor, main_loop
    
    # 获取当前事件循环
    main_loop = asyncio.get_running_loop()
    
    # 创建必要的目录
    os.makedirs('config', exist_ok=True)
    os.makedirs('logs', exist_ok=True)
    os.makedirs('modules', exist_ok=True)
    
    # 检查并创建 modules/__init__.py
    init_file = os.path.join('modules', '__init__.py')
    if not os.path.exists(init_file):
        with open(init_file, 'w', encoding='utf-8') as f:
            f.write('"""ProxyCat 模块包初始化文件"""\n')
    
    # 设置信号处理
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # 🔐 初始化安全配置
    init_security_config()
    
    # 加载配置
    config = load_simple_config()
    proxy_stats.update({
        'target_country': config.get('target_country', 'US'),
        'mode': config.get('mode', 'country'),
        'language': config.get('language', 'cn'),
        'use_getip': config.get('use_getip', 'True').lower() == 'true',
        'port': int(config.get('port', '1080')),
        'web_port': int(config.get('web_port', '5000'))
    })
    
    # 初始化国家监控
    country_monitor = init_country_monitor()
    
    # 打印启动信息（增强安全版）
    print("\n" + "="*70)
    print("🐱 ProxyCat - 智能代理池管理系统 (增强安全版)")
    print("="*70)
    print(f"🚀 SOCKS5 代理端口: {proxy_stats['port']} (用户名: {security_config['socks5_username']})")
    print(f"🌐 Web 管理界面: http://localhost:{proxy_stats['web_port']}{security_config['web_access_suffix']}")
    print(f"🎯 目标国家: {proxy_stats['target_country']}")
    print(f"🤖 自动监控间隔: {country_monitor.check_interval}秒")
    
    # 显示安全功能状态
    print("🔐 安全功能状态:")
    print(f"   ✅ SOCKS5认证: 启用 (用户: {security_config['socks5_username']})")
    print(f"   ✅ Web访问后缀: {security_config['web_access_suffix']}")
    
    if security_config['web_allowed_ips']:
        print(f"   ✅ IP访问限制: 启用 ({len(security_config['web_allowed_ips'])} 个允许的IP/网段)")
    else:
        print("   ⚠️  IP访问限制: 禁用")
    
    print(f"   {'✅' if security_config['enable_access_log'] else '❌'} 访问日志: {'启用' if security_config['enable_access_log'] else '禁用'}")
    
    # 显示黑名单状态
    if country_monitor and hasattr(country_monitor, 'get_blacklist_stats'):
        try:
            blacklist_stats = country_monitor.get_blacklist_stats()
            if blacklist_stats['enabled']:
                if blacklist_stats['loaded']:
                    source_text = {
                        'local': '本地缓存',
                        'remote': '远程下载',
                        'remote_sync': '远程同步',
                        'remote_async': '远程异步'
                    }.get(blacklist_stats['source'], '未知')
                    
                    print(f"🛡️  IP黑名单: ✅ 已加载 ({blacklist_stats['size']} 条记录, 来源: {source_text})")
                    
                    if blacklist_stats['needs_update']:
                        print("⏰ 黑名单将在后台自动更新")
                    else:
                        hours_old = blacklist_stats['hours_since_update']
                        print(f"📅 黑名单状态: 最新 (上次更新: {hours_old:.1f}小时前)")
                else:
                    print("🛡️  IP黑名单: ❌ 加载失败")
            else:
                print("🛡️  IP黑名单: 🚫 功能已禁用")
        except Exception as e:
            print("🛡️  IP黑名单: ⚠️ 状态检查失败")
            logging.debug(f"黑名单状态检查失败: {e}")
    else:
        print("🛡️  IP黑名单: ⚠️ 功能不可用")
    
    print("="*70)
    
    # 检查 getip 模块
    getip_func = safe_import_getip()
    if getip_func:
        print("✅ getip 模块加载成功")
    else:
        print("❌ getip 模块加载失败")
        print("   请确保 modules/getip.py 文件存在且配置正确")
    
    print("="*70)
    print("💡 使用提示:")
    print(f"   1. 访问 http://localhost:{proxy_stats['web_port']}{security_config['web_access_suffix']} 管理代理")
    print(f"   2. SOCKS5代理: localhost:{proxy_stats['port']} (账号: {security_config['socks5_username']})")
    print("   3. 黑名单定时更新已修复，每5分钟检查一次")
    print("   4. 强制更新功能已修复，Web界面按钮正常工作")
    print("   5. 增强安全功能已启用，包括访问控制和认证")
    print("="*70)
    
    # 启动Flask应用（在单独线程中）
    flask_thread = threading.Thread(
        target=run_flask_app, 
        args=(proxy_stats['web_port'],), 
        daemon=True
    )
    flask_thread.start()
    
    # 等待Flask启动
    await asyncio.sleep(2)
    
    # 启动 SOCKS5 服务器（主线程）
    try:
        await start_socks_server()
    except KeyboardInterrupt:
        logging.info("🛑 接收到中断信号")
    except Exception as e:
        logging.error(f"❌ 程序运行错误: {e}")
    finally:
        if socks_server:
            await socks_server.stop()
        if country_monitor:
            country_monitor.stop_monitoring()

if __name__ == '__main__':
    # 设置日志
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler('logs/proxycat.log', encoding='utf-8')
        ]
    )
    
    # 运行主函数
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🛑 程序已退出")
    except Exception as e:
        logging.error(f"❌ 程序启动失败: {e}")
        import traceback
        traceback.print_exc()       
