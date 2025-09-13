# 修复 modules/proxyserver.py 中的 AsyncProxyServer 类

import asyncio
import logging
from typing import Optional
from .country_proxy_manager import CountryBasedProxyManager

class AsyncProxyServer:
    """异步代理服务器类 - 修复版"""
    
    def __init__(self, config):
        # 原有的初始化代码...
        self.config = config
        self.mode = config.get('mode', 'cycle')
        self.language = config.get('language', 'cn')  # 添加 language 属性
        self.use_getip = config.get('use_getip', 'False').lower() == 'true'
        self.port = int(config.get('port', '1080'))
        self.web_port = int(config.get('web_port', '5000'))
        
        # 代理相关属性
        self.current_proxy = None
        self.proxy_cycle = None
        self.proxies = []
        self.proxy_username = config.get('proxy_username', '')
        self.proxy_password = config.get('proxy_password', '')
        
        # 服务器状态
        self.stop_server = False
        self.server = None
        
        # 新增：国家检测管理器
        if self.mode == 'country':
            self.country_manager = CountryBasedProxyManager(config)
            self.monitoring_task = None
        else:
            self.country_manager = None
            self.monitoring_task = None
            
        # 其他配置
        self.interval = int(config.get('interval', '300'))
        self.display_level = int(config.get('display_level', '1'))
        self.check_proxies = config.get('check_proxies', 'False').lower() == 'true'
    
    def _update_config_values(self, new_config):
        """更新配置值"""
        self.mode = new_config.get('mode', self.mode)
        self.language = new_config.get('language', self.language)
        self.use_getip = new_config.get('use_getip', 'False').lower() == 'true'
        self.port = int(new_config.get('port', self.port))
        self.web_port = int(new_config.get('web_port', self.web_port))
        self.interval = int(new_config.get('interval', self.interval))
        self.display_level = int(new_config.get('display_level', self.display_level))
        self.check_proxies = new_config.get('check_proxies', 'False').lower() == 'true'
        
        # 更新代理认证信息
        self.proxy_username = new_config.get('proxy_username', self.proxy_username)
        self.proxy_password = new_config.get('proxy_password', self.proxy_password)
    
    async def start(self):
        """启动代理服务器"""
        logging.info("启动代理服务器...")
        
        # 如果是国家模式，启动国家监控
        if self.mode == 'country' and self.country_manager:
            logging.info("启动基于国家的智能代理切换模式")
            
            # 首次获取代理
            if self.use_getip:
                try:
                    from modules.getip import newip
                    initial_proxy = newip()
                    if initial_proxy:
                        self.current_proxy = initial_proxy
                        self.country_manager.set_current_proxy(initial_proxy)
                        logging.info(f"初始代理: {initial_proxy}")
                    else:
                        logging.warning("无法获取初始代理")
                except Exception as e:
                    logging.error(f"获取初始代理失败: {e}")
            
            # 启动监控任务
            self.monitoring_task = asyncio.create_task(
                self.country_manager.start_monitoring(
                    self._get_new_proxy_async,
                    self._switch_proxy_async
                )
            )
        
        # 启动 SOCKS5 代理服务器
        try:
            self.server = await asyncio.start_server(
                self.handle_client,
                '0.0.0.0',
                self.port
            )
            logging.info(f"SOCKS5 代理服务器已启动，端口: {self.port}")
            
            # 如果需要，可以在这里启动服务器
            async with self.server:
                await self.server.serve_forever()
                
        except Exception as e:
            logging.error(f"启动代理服务器失败: {e}")
            raise
    
    async def stop(self):
        """停止代理服务器"""
        self.stop_server = True
        
        # 停止国家监控
        if self.country_manager:
            self.country_manager.stop_monitoring()
            
        if self.monitoring_task:
            self.monitoring_task.cancel()
            try:
                await self.monitoring_task
            except asyncio.CancelledError:
                pass
        
        # 停止服务器
        if self.server:
            self.server.close()
            await self.server.wait_closed()
        
        logging.info("代理服务器已停止")
    
    async def handle_client(self, reader, writer):
        """处理客户端连接 - SOCKS5 协议处理"""
        try:
            # 这里应该实现完整的 SOCKS5 协议处理
            # 为了简化，这里只是一个基本框架
            
            # 获取当前代理
            current_proxy = self.get_current_proxy()
            if not current_proxy:
                logging.error("没有可用的代理")
                writer.close()
                await writer.wait_closed()
                return
            
            # 实际的 SOCKS5 协议处理逻辑应该在这里实现
            # 包括握手、认证、连接建立等步骤
            
            # 这里需要根据原始代码补充完整的 SOCKS5 处理逻辑
            
        except Exception as e:
            logging.error(f"处理客户端连接时发生错误: {e}")
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except:
                pass
    
    async def _get_new_proxy_async(self) -> Optional[str]:
        """异步获取新代理"""
        if self.use_getip:
            try:
                # 在线程池中运行同步的 newip 函数
                loop = asyncio.get_event_loop()
                from modules.getip import newip
                proxy = await loop.run_in_executor(None, newip)
                return proxy
            except Exception as e:
                logging.error(f"获取新代理失败: {e}")
                return None
        return None
    
    async def _switch_proxy_async(self, new_proxy: str):
        """异步切换代理"""
        old_proxy = self.current_proxy
        self.current_proxy = new_proxy
        
        # 更新国家管理器的当前代理
        if self.country_manager:
            self.country_manager.set_current_proxy(new_proxy)
        
        logging.info(f"代理已切换: {old_proxy} -> {new_proxy}")
    
    def get_current_proxy(self) -> Optional[str]:
        """获取当前代理"""
        return self.current_proxy
    
    def get_stats(self) -> dict:
        """获取统计信息"""
        stats = {
            'mode': self.mode,
            'current_proxy': self.current_proxy,
            'use_getip': self.use_getip,
            'language': self.language,
            'port': self.port,
            'web_port': self.web_port
        }
        
        # 如果是国家模式，添加国家相关统计
        if self.country_manager:
            stats.update(self.country_manager.get_stats())
        
        return stats
    
    async def manual_switch_proxy(self) -> bool:
        """手动切换代理"""
        if self.mode == 'country' and self.country_manager:
            try:
                new_proxy = await self._get_new_proxy_async()
                if new_proxy:
                    await self._switch_proxy_async(new_proxy)
                    return True
                return False
            except Exception as e:
                logging.error(f"手动切换代理失败: {e}")
                return False
        else:
            # 对于其他模式的手动切换逻辑
            try:
                if self.use_getip:
                    new_proxy = await self._get_new_proxy_async()
                    if new_proxy:
                        self.current_proxy = new_proxy
                        logging.info(f"手动切换代理成功: {new_proxy}")
                        return True
                return False
            except Exception as e:
                logging.error(f"手动切换代理失败: {e}")
                return False
    
    def set_target_country(self, country_code: str):
        """设置目标国家"""
        if self.country_manager:
            self.country_manager.target_country = country_code.upper()
            logging.info(f"目标国家已设置为: {country_code}")
    
    def get_target_country(self) -> str:
        """获取目标国家"""
        if self.country_manager:
            return self.country_manager.target_country
        return self.config.get('target_country', 'US')

# 修复主程序文件 app.py 中的错误

import asyncio
import logging
import signal
import sys
from modules.proxyserver import AsyncProxyServer
from modules.modules import load_config, print_banner, get_message
from flask import Flask, request, jsonify, render_template
import threading

# 全局变量
proxy_server = None
app = Flask(__name__)

def signal_handler(signum, frame):
    """信号处理器"""
    print('\n正在关闭服务器...')
    if proxy_server:
        # 停止服务器
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(proxy_server.stop())
            else:
                loop.run_until_complete(proxy_server.stop())
        except:
            pass
    sys.exit(0)

# Web API 路由
@app.route('/')
def index():
    """主页"""
    return render_template('index.html')

@app.route('/api/proxy/stats')
def get_proxy_stats():
    """获取代理统计信息"""
    try:
        if proxy_server:
            stats = proxy_server.get_stats()
            return jsonify({
                'success': True,
                'data': stats
            })
        else:
            return jsonify({
                'success': False,
                'error': 'Proxy server not initialized'
            }), 500
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/proxy/switch', methods=['POST'])
def manual_switch():
    """手动切换代理"""
    try:
        if not proxy_server:
            return jsonify({
                'success': False,
                'error': 'Proxy server not initialized'
            }), 500
            
        # 在新的事件循环中运行异步操作
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            success = loop.run_until_complete(proxy_server.manual_switch_proxy())
            return jsonify({
                'success': success,
                'message': '代理切换成功' if success else '代理切换失败'
            })
        finally:
            loop.close()
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/proxy/country', methods=['GET', 'POST'])
def manage_target_country():
    """获取或设置目标国家"""
    if not proxy_server:
        return jsonify({
            'success': False,
            'error': 'Proxy server not initialized'
        }), 500
        
    if request.method == 'GET':
        try:
            country = proxy_server.get_target_country()
            return jsonify({
                'success': True,
                'target_country': country
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
            
            proxy_server.set_target_country(country)
            return jsonify({
                'success': True,
                'message': f'目标国家已设置为: {country}'
            })
        except Exception as e:
            return jsonify({
                'success': False,
                'error': str(e)
            }), 500

@app.route('/api/proxy/blacklist/update', methods=['POST'])
def update_blacklist():
    """手动更新IP黑名单"""
    try:
        if not proxy_server or not proxy_server.country_manager:
            return jsonify({
                'success': False,
                'error': '当前模式不支持IP黑名单功能'
            }), 400
            
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            success = loop.run_until_complete(proxy_server.country_manager.update_ip_blacklist())
            return jsonify({
                'success': success,
                'message': 'IP黑名单更新成功' if success else 'IP黑名单更新失败'
            })
        finally:
            loop.close()
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

def run_flask_app():
    """在单独线程中运行Flask应用"""
    app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)

async def main():
    """主函数"""
    global proxy_server
    
    # 设置信号处理
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    try:
        # 加载配置
        config = load_config('config/config.ini')
        
        # 打印横幅
        print_banner(config)
        
        # 创建代理服务器实例
        proxy_server = AsyncProxyServer(config)
        
        # 启动Flask应用（在单独线程中）
        flask_thread = threading.Thread(target=run_flask_app, daemon=True)
        flask_thread.start()
        
        # 显示Web面板URL
        web_url = f"http://localhost:{proxy_server.web_port}"
        logging.info(get_message('web_panel_url', proxy_server.language, web_url))
        
        # 如果是国家模式，打印额外信息
        if config.get('mode') == 'country':
            target_country = config.get('target_country', 'US')
            check_interval = config.get('country_check_interval', 60)
            logging.info(f"🌍 智能国家检测模式已启用")
            logging.info(f"🎯 目标国家: {target_country}")
            logging.info(f"⏱️  检测间隔: {check_interval}秒")
            logging.info(f"🛡️  IP黑名单: {'启用' if config.get('enable_ip_blacklist', True) else '禁用'}")
        
        # 启动代理服务器
        await proxy_server.start()
            
    except KeyboardInterrupt:
        logging.info("接收到中断信号，正在关闭服务器...")
    except Exception as e:
        logging.error(f"服务器运行错误: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if proxy_server:
            await proxy_server.stop()

if __name__ == '__main__':
    # 设置日志
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler('proxycat.log', encoding='utf-8')
        ]
    )
    
    # 运行主函数
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("程序已退出")
