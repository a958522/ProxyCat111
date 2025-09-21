# modules/getip.py
# 支持带账户密码的代理获取 - 针对你的API格式优化

import json
import time
import random
import requests
import logging
import os

def load_config():
    """简化的配置加载函数"""
    import os
    import configparser
    
    config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config', 'config.ini')
    config = {
        'language': 'cn',
        'getip_url': '',
        'buy_url_template': '',
        'proxy_username': '',      # 备用固定认证用户名
        'proxy_password': '',      # 备用固定认证密码
        'use_api_auth': 'True',    # 优先使用API返回的认证信息
        'fallback_to_fixed': 'True' # API无认证时是否fallback到固定认证
    }
    
    if os.path.exists(config_path):
        try:
            parser = configparser.ConfigParser()
            parser.read(config_path, encoding='utf-8')
            
            section = 'DEFAULT' if 'DEFAULT' in parser else parser.sections()[0] if parser.sections() else 'DEFAULT'
            
            for key in config.keys():
                if parser.has_option(section, key):
                    config[key] = parser.get(section, key)
        except Exception as e:
            logging.error(f"读取配置文件失败: {e}")
    
    return config

def get_message(key, language, *args):
    """简化的消息获取函数"""
    messages = {
        'whitelist_error': '白名单错误',
        'proxy_file_not_found': '代理文件未找到'
    }
    return messages.get(key, key)

def newip():
    """获取新代理IP的主函数 - 支持API返回的认证信息"""
    config = load_config()
    language = config.get('language', 'cn')
    
    def handle_error(error_type, details=None):
        error_msg = 'whitelist_error' if error_type == 'whitelist' else 'proxy_file_not_found'
        print(get_message(error_msg, language, str(details)))
        raise ValueError(f"{error_type}: {details}")
    
    try:
        # 配置信息
        list_url = config.get('getip_url', '')
        buy_url_template = config.get('buy_url_template', '')
        
        # 认证配置
        fixed_username = config.get('proxy_username', '')
        fixed_password = config.get('proxy_password', '')
        use_api_auth = config.get('use_api_auth', 'True').lower() == 'true'
        fallback_to_fixed = config.get('fallback_to_fixed', 'True').lower() == 'true'
        
        # 🔥 ISP过滤配置（使用手动解析确保正确读取）
        
        # 🔧 直接读取配置文件确保正确性
        config_path = '/proxy-1/config/config.ini'
        exclude_isps_config = 'verizon,rcn'  # 默认值
        
        if os.path.exists(config_path):
            try:
                with open(config_path, 'r', encoding='utf-8') as f:
                    lines = f.readlines()
                
                for line_num, line in enumerate(lines, 1):
                    line_stripped = line.strip()
                    if '=' in line_stripped and 'exclude_isps' in line_stripped:
                        key, value = line_stripped.split('=', 1)
                        if key.strip() == 'exclude_isps':
                            exclude_isps_config = value.strip()
                            print(f"🔧 直接读取exclude_isps (第{line_num}行): '{exclude_isps_config}'")
                            break
            except Exception as e:
                print(f"🔧 直接读取失败，使用配置对象: {e}")
                exclude_isps_config = config.get('exclude_isps', 'verizon,rcn')
        else:
            exclude_isps_config = config.get('exclude_isps', 'verizon,rcn')
        
        # 🔧 支持环境变量覆盖（优先级最高）
        env_exclude_isps = os.getenv('EXCLUDE_ISPS')
        if env_exclude_isps:
            exclude_isps_config = env_exclude_isps
            print(f"🔧 使用环境变量EXCLUDE_ISPS: '{exclude_isps_config}'")
        
        # 处理空配置的情况
        if exclude_isps_config.strip():
            exclude_isps = [isp.strip().lower() for isp in exclude_isps_config.split(',') if isp.strip()]
        else:
            exclude_isps = []
        
        # 显示当前的ISP过滤配置
        if exclude_isps:
            print(f"🔧 当前ISP过滤配置: {exclude_isps}")
        else:
            print(f"🔧 ISP过滤已禁用（exclude_isps为空）")
        
        if not list_url:
            raise ValueError('getip_url 配置为空，请在 config.ini 中设置 getip_url')
        
        def get_proxy_list():
            """获取代理列表并随机选择一个ID"""
            print(f"正在获取代理列表: {list_url}")
            response = requests.get(list_url, timeout=10)
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
                
                # 🔥 简化的过滤系统：只过滤ISP
                filtered_proxy_list = []
                total_count = len(proxy_list)
                excluded_by_isp = 0
                
                for proxy in proxy_list:
                    host = proxy.get('host', '').lower()
                    proxy_id = proxy.get('id', 'unknown')
                    
                    # 检查ISP排除列表
                    excluded_by_current_isp = False
                    if exclude_isps:
                        for excluded_isp in exclude_isps:
                            if excluded_isp and excluded_isp in host:
                                excluded_by_isp += 1
                                excluded_by_current_isp = True
                                break
                    
                    if excluded_by_current_isp:
                        continue
                    
                    filtered_proxy_list.append(proxy)
                
                # 简化的过滤统计信息
                print(f"📊 代理筛选统计:")
                print(f"   总代理数量: {total_count}")
                print(f"   ISP过滤排除: {excluded_by_isp} 个")
                print(f"   符合条件的代理: {len(filtered_proxy_list)} 个")
                
                if exclude_isps:
                    print(f"   排除的ISP关键字: {', '.join(exclude_isps)}")
                
                if not filtered_proxy_list:
                    error_msg = "过滤后的代理列表为空。"
                    if exclude_isps:
                        error_msg += f" 所有代理都包含被排除的ISP关键字: {exclude_isps}。"
                    else:
                        error_msg += " 原始代理列表为空。"
                    raise ValueError(error_msg)
                
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
            """通过ID购买/获取具体代理信息 - 处理新的API格式"""
            buy_url = f"{buy_url_template}{proxy_id}"
            print(f"获取代理详情: {buy_url}")
            
            response = requests.get(buy_url, timeout=10)
            response.raise_for_status()
            
            try:
                data = json.loads(response.text)
                
                # 检查API状态
                if data.get('status', {}).get('code') != '1000':
                    error_msg = data.get('status', {}).get('message', 'Unknown API error')
                    raise ValueError(f"获取代理详情API返回错误: {error_msg}")
                
                # 解析代理数据
                proxy_data = data.get('data', {})
                
                # 🔥 提取基本信息
                ip = proxy_data.get('ipaddress')
                port = proxy_data.get('port')
                
                if not ip or not port:
                    raise ValueError("API响应中缺少IP或端口信息")
                
                # 🔥 提取认证信息（支持你的新API格式）
                api_username = proxy_data.get('username', '').strip()
                api_password = proxy_data.get('password', '').strip()
                
                # 决定最终使用的认证信息
                final_username = None
                final_password = None
                auth_source = "none"
                
                if use_api_auth and api_username and api_password:
                    # 优先使用API返回的认证信息
                    final_username = api_username
                    final_password = api_password
                    auth_source = "api"
                    print(f"✅ 使用API返回的认证信息: {api_username}:***")
                    
                elif fallback_to_fixed and fixed_username and fixed_password:
                    # fallback到配置的固定认证信息
                    final_username = fixed_username
                    final_password = fixed_password
                    auth_source = "fixed"
                    print(f"⚠️ API未提供认证信息，使用配置的固定认证: {fixed_username}:***")
                    
                else:
                    # 无认证
                    print(f"ℹ️ 使用无认证代理: {ip}:{port}")
                
                # 显示代理详细信息
                print(f"代理详情:")
                print(f"  - IP: {ip}")
                print(f"  - 端口: {port}")
                print(f"  - 位置: {proxy_data.get('city', 'Unknown')}, {proxy_data.get('region', 'Unknown')}")
                print(f"  - ISP: {proxy_data.get('host', 'Unknown')}")
                
                # 只有当API确实返回了类型信息时才显示
                proxy_type = proxy_data.get('is_type', '')
                if proxy_type and proxy_type != 'Unknown':
                    print(f"  - 类型: {proxy_type}")
                
                print(f"  - 认证: {auth_source}")
                
                # 构造代理字符串
                if final_username and final_password:
                    proxy_string = f"{final_username}:{final_password}@{ip}:{port}"
                else:
                    proxy_string = f"{ip}:{port}"
                
                return proxy_string
                
            except json.JSONDecodeError:
                raise ValueError("获取代理详情API响应不是有效的JSON格式")
        
        # 执行获取流程
        print("="*50)
        print("🚀 开始获取代理...")
        
        # 第一步：获取代理列表并选择ID
        proxy_id = get_proxy_list()
        
        # 第二步：通过ID获取具体代理信息
        proxy = buy_proxy(proxy_id)
        
        # 处理特殊错误代码（保留原有逻辑）
        if proxy == "error000x-13":
            print("⚠️ 检测到白名单错误，尝试添加白名单...")
            appKey = ""
            anquanma = ""
            whitelist_url = f"https://sch.shanchendaili.com/api.html?action=addWhiteList&appKey={appKey}&anquanma={anquanma}"
            requests.get(whitelist_url).raise_for_status()
            time.sleep(1)
            
            # 重新获取
            proxy_id = get_proxy_list()
            proxy = buy_proxy(proxy_id)
        
        # 🔥 构造最终的SOCKS5代理URL
        result = f"socks5://{proxy}"
        
        print("="*50)
        print(f"✅ 代理获取成功!")
        print(f"📋 最终代理: {result}")
        print("="*50)
        
        return result
        
    except requests.RequestException as e:
        handle_error('request', e)
    except ValueError as e:
        handle_error('config', e)
    except Exception as e:
        handle_error('unknown', e)

def debug_config():
    """调试配置加载情况"""
    print("🔧 调试配置加载情况:")
    print("="*50)
    
    import os
    config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config', 'config.ini')
    print(f"配置文件路径: {config_path}")
    print(f"配置文件是否存在: {os.path.exists(config_path)}")
    
    if os.path.exists(config_path):
        print(f"\n配置文件内容:")
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
                for i, line in enumerate(lines, 1):
                    if 'exclude_isps' in line.lower():
                        print(f"  第{i}行: {line.strip()} ⭐")
                    elif line.strip() and not line.strip().startswith('#'):
                        print(f"  第{i}行: {line.strip()}")
        except Exception as e:
            print(f"读取配置文件失败: {e}")
    
    # 🔧 使用修复后的配置加载逻辑
    config = load_config()
    
    print(f"\n解析后的配置:")
    for key, value in config.items():
        if 'password' in key.lower():
            print(f"  {key} = {'*' * len(str(value)) if value else '(空)'}")
        else:
            print(f"  {key} = {value}")
    
    print("\n🔧 ISP过滤解析结果:")
    exclude_isps_config = config.get('exclude_isps', 'verizon,rcn')
    print(f"  最终配置值: '{exclude_isps_config}'")
    
    if exclude_isps_config.strip():
        exclude_isps = [isp.strip().lower() for isp in exclude_isps_config.split(',') if isp.strip()]
        print(f"  解析后的ISP列表: {exclude_isps}")
        print(f"  ISP过滤状态: 启用")
    else:
        print(f"  解析后的ISP列表: []")
        print(f"  ISP过滤状态: 禁用")
    
    print("="*50)

def test_isp_filtering():
    """测试ISP过滤功能"""
    print("🧪 测试ISP过滤功能:")
    print("="*50)
    
    # 模拟一些代理数据
    test_proxies = [
        {"id": "1", "host": "Verizon Communications Inc.", "ipaddress": "1.1.1.1"},
        {"id": "2", "host": "T-Mobile USA, Inc.", "ipaddress": "2.2.2.2"},
        {"id": "3", "host": "Cox Communications", "ipaddress": "3.3.3.3"},
        {"id": "4", "host": "AT&T Services Inc.", "ipaddress": "4.4.4.4"},
        {"id": "5", "host": "Spectrum Internet Services", "ipaddress": "5.5.5.5"},
    ]
    
    config = load_config()
    exclude_isps_config = config.get('exclude_isps', 'verizon,rcn')
    
    if exclude_isps_config.strip():
        exclude_isps = [isp.strip().lower() for isp in exclude_isps_config.split(',') if isp.strip()]
    else:
        exclude_isps = []
    
    print(f"当前排除的ISP: {exclude_isps}")
    print("\n代理过滤测试:")
    
    for proxy in test_proxies:
        host = proxy.get('host', '').lower()
        proxy_id = proxy.get('id')
        
        excluded = False
        matched_isp = None
        
        if exclude_isps:
            for excluded_isp in exclude_isps:
                if excluded_isp and excluded_isp in host:
                    excluded = True
                    matched_isp = excluded_isp
                    break
        
        status = "❌ 被排除" if excluded else "✅ 通过"
        reason = f" (匹配: '{matched_isp}')" if matched_isp else ""
        
        print(f"  代理{proxy_id}: {proxy['host']} -> {status}{reason}")
    
    print("="*50)
def test_proxy_format():
    """测试不同的代理格式"""
    print("🧪 测试代理格式解析:")
    print("="*50)
    
    # 模拟API响应测试
    test_data_with_auth = {
        "status": {"code": "1000", "message": "Success"},
        "data": {
            "ipaddress": "152.53.36.101",
            "port": "1080",
            "username": "DVS-mc9-D496",
            "password": "n3d8toW",
            "city": "Los Angeles",
            "region": "California"
        }
    }
    
    test_data_no_auth = {
        "status": {"code": "1000", "message": "Success"},
        "data": {
            "ipaddress": "152.53.36.101",
            "port": "1080",
            "username": "",
            "password": "",
            "city": "Los Angeles",
            "region": "California"
        }
    }
    
    print("1. 带认证信息的代理:")
    proxy_data = test_data_with_auth['data']
    username = proxy_data.get('username', '').strip()
    password = proxy_data.get('password', '').strip()
    ip = proxy_data.get('ipaddress')
    port = proxy_data.get('port')
    
    if username and password:
        result = f"socks5://{username}:{password}@{ip}:{port}"
        print(f"   结果: {result}")
    else:
        result = f"socks5://{ip}:{port}"
        print(f"   结果: {result}")
    
    print("\n2. 无认证信息的代理:")
    proxy_data = test_data_no_auth['data']
    username = proxy_data.get('username', '').strip()
    password = proxy_data.get('password', '').strip()
    ip = proxy_data.get('ipaddress')
    port = proxy_data.get('port')
    
    if username and password:
        result = f"socks5://{username}:{password}@{ip}:{port}"
        print(f"   结果: {result}")
    else:
        result = f"socks5://{ip}:{port}"
        print(f"   结果: {result}")
    
    print("="*50)

# 测试函数
if __name__ == "__main__":
    print("🔧 ProxyCat - 代理获取模块测试")
    print("="*70)
    
    import sys
    
    if len(sys.argv) > 1:
        if sys.argv[1] == "debug":
            debug_config()
            exit()
        elif sys.argv[1] == "test-isp":
            test_isp_filtering()
            exit()
        elif sys.argv[1] == "test-format":
            test_proxy_format()
            exit()
    
    print("可用的测试命令:")
    print("  python3 getip.py debug       - 调试配置加载")
    print("  python3 getip.py test-isp    - 测试ISP过滤")
    print("  python3 getip.py test-format - 测试代理格式")
    print("  python3 getip.py             - 实际获取代理")
    print()
    
    # 先调试配置
    debug_config()
    print()
    
    # 测试ISP过滤
    test_isp_filtering()
    print()
    
    # 先测试格式解析
    test_proxy_format()
    
    print("\n🚀 实际代理获取测试:")
    try:
        proxy = newip()
        print(f"✅ 测试成功: {proxy}")
    except Exception as e:
        print(f"❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
