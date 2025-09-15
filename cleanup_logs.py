#!/usr/bin/env python3
import os
import time
import glob

def cleanup_logs():
    logs_dir = '/app/logs'  # Docker容器内的路径
    max_age_days = 3        # 保留7天的日志
    max_size_mb = 50       # 单文件最大100MB
    
    if not os.path.exists(logs_dir):
        print(f"日志目录不存在: {logs_dir}")
        return
    
    current_time = time.time()
    cutoff_time = current_time - (max_age_days * 24 * 3600)
    cleaned_count = 0
    total_freed_mb = 0
    
    log_files = glob.glob(os.path.join(logs_dir, '*.log*'))
    
    print(f"检查日志目录: {logs_dir}")
    print(f"找到 {len(log_files)} 个日志文件")
    
    for log_file in log_files:
        try:
            file_stat = os.stat(log_file)
            file_size_mb = file_stat.st_size / (1024 * 1024)
            file_age = file_stat.st_mtime
            file_age_days = (current_time - file_age) / (24 * 3600)
            
            should_delete = False
            reason = ""
            
            if file_age < cutoff_time:
                should_delete = True
                reason = f"过期({file_age_days:.1f}天 > {max_age_days}天)"
            elif file_size_mb > max_size_mb:
                should_delete = True
                reason = f"过大({file_size_mb:.1f}MB > {max_size_mb}MB)"
            
            if should_delete:
                os.remove(log_file)
                cleaned_count += 1
                total_freed_mb += file_size_mb
                print(f"✅ 删除: {os.path.basename(log_file)} - {reason}")
            else:
                print(f"⏭️  保留: {os.path.basename(log_file)} - {file_age_days:.1f}天, {file_size_mb:.1f}MB")
                
        except Exception as e:
            print(f"❌ 清理失败 {log_file}: {e}")
    
    if cleaned_count > 0:
        print(f"🎉 清理完成: 删除 {cleaned_count} 个文件，释放 {total_freed_mb:.1f}MB 空间")
    else:
        print("✨ 无需清理，所有日志文件都符合保留条件")

if __name__ == '__main__':
    print("🗑️  开始清理日志文件...")
    cleanup_logs()
    print("🗑️  日志清理完成")
