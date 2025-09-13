from modules.modules import get_message, load_config
import requests
import json
import time
import random

def newip():
    config = load_config()
    language = config.get('language', 'cn')
    
    def handle_error(error_type, details=None):
        error_msg = 'whitelist_error' if error_type == 'whitelist' else 'proxy_file_not_found'
        print(get_message(error_msg, language, str(details)))
        raise ValueError(f"{error_type}: {details}")
    
    try:
        # 配置信息
        list_url = config.get('getip_url', '')  # 获取代理列表的API
        buy_url_template = config.get('buy_url_template', '')
        username = config.get('proxy_username', '')
        password = config.get('proxy_password', '')
        
        if not list_url:
            raise ValueError('getip_url')
        
        def get_proxy_list():
            """获取代理列表并随机选择一个ID"""
            response = requests.get(list_url)
            response.raise_for_status()
            
            try:
                data = json.loads(response.text)
                
                # 检查API状态
                if data.get('status', {}).get('code') != '1000':
                    error_msg = data.get('status', {}).get('message', 'Unknown API error')
                    raise ValueError(f"获取代理列表API返回错误: {error_msg}")
                
                # 获取代理列表
                proxy_list = data.get('data', [])
                
                if not proxy_list:
                    raise ValueError("代理列表为空")
                
                # 过滤掉host中包含"Verizon"或"RCN"的代理
                filtered_proxy_list = []
                for proxy in proxy_list:
                    host = proxy.get('host', '').lower()
                    if 'verizon' not in host and 'rcn' not in host:
                        filtered_proxy_list.append(proxy)
                
                if not filtered_proxy_list:
                    raise ValueError("过滤后的代理列表为空（所有代理都包含Verizon或RCN）")
                
                print(f"原始代理数量: {len(proxy_list)}, 过滤后数量: {len(filtered_proxy_list)}")
                
                # 从过滤后的列表中随机选择一个代理的ID
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
                
                # 检查API状态
                if data.get('status', {}).get('code') != '1000':
                    error_msg = data.get('status', {}).get('message', 'Unknown API error')
                    raise ValueError(f"获取代理详情API返回错误: {error_msg}")
                
                # 提取IP和端口
                proxy_data = data.get('data', {})
                ip = proxy_data.get('ipaddress')
                port = proxy_data.get('port')
                
                if not ip or not port:
                    raise ValueError("API响应中缺少IP或端口信息")
                
                print(f"获取到代理: {ip}:{port}")
                return f"{ip}:{port}"
                
            except json.JSONDecodeError:
                raise ValueError("获取代理详情API响应不是有效的JSON格式")
        
        # 第一步：获取代理列表并选择ID
        proxy_id = get_proxy_list()
        
        # 第二步：通过ID获取具体代理信息
        proxy = buy_proxy(proxy_id)
        
        # 处理特殊错误代码（如果需要的话）
        if proxy == "error000x-13":
            appKey = ""
            anquanma = ""
            whitelist_url = f"https://sch.shanchendaili.com/api.html?action=addWhiteList&appKey={appKey}&anquanma={anquanma}"
            requests.get(whitelist_url).raise_for_status()
            time.sleep(1)
            # 重新获取
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
