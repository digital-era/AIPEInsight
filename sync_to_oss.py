#@title Commit Indutryinsight to OSS
import requests
import oss2
import os
# --- 1. GitHub 配置信息 ---
GITHUB_OWNER = "digital-era"
GITHUB_REPO = "AIPEInsight"
# 我们将直接从 raw content URL 下载，所以 branch 很重要
GITHUB_BRANCH = "main"

# --- 2. 阿里云 OSS 配置信息 ---
# 警告：请勿将 AccessKey 硬编码在代码中并公开，环境变量中读取密钥，这样更安全
OSS_ACCESS_KEY_ID = os.getenv("OSS_ACCESS_KEY_ID")
OSS_ACCESS_KEY_SECRET = os.getenv("OSS_ACCESS_KEY_SECRET")

# 从环境变量中安全地读取 GITHUB_TOKEN
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

# 检查密钥是否存在
if not OSS_ACCESS_KEY_ID or not OSS_ACCESS_KEY_SECRET:
    raise ValueError("错误：环境变量 OSS_ACCESS_KEY_ID 或 OSS_ACCESS_KEY_SECRET 未设置！")

OSS_REGION = "cn-hangzhou"
OSS_ENDPOINT = f"https://oss-{OSS_REGION}.aliyuncs.com" # 使用 https 更安全
OSS_BUCKET_NAME = "aiep-users"
OSS_TARGET_DIRECTORY = "deepreport/" # 目标目录



def get_github_files_to_sync(branch="main"):
    # 使用 Git Trees API，需要指定一个 tree_sha。我们可以直接使用分支名，API 会自动解析为该分支最新的 commit SHA。
    # recursive=1 是关键，它会递归获取所有子目录中的文件。
    api_url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/git/trees/{branch}?recursive=1"
    print(f"Step 1: 从 GitHub Git Trees API 获取全量文件列表...\nURL: {api_url}")

    headers = {
        'Accept': 'application/vnd.github.v3+json',
    }
    # 如果提供了 Token，则加入 Authorization 头，能提高 API 调用速率限制，并能访问私有仓库。
    if GITHUB_TOKEN:
        headers['Authorization'] = f'token {GITHUB_TOKEN}'

    file_list = []
    try:
        response = requests.get(api_url, headers=headers)
        # 检查请求是否成功 (例如 404 Not Found, 403 Forbidden)
        response.raise_for_status()
        data = response.json()

        # 检查 'tree' 键是否存在，以及 API 是否因为仓库过大而返回了截断（truncated）的结果
        if 'tree' not in data:
            print("错误：API 响应中未找到 'tree' 键。")
            return []
        
        if data.get('truncated', False):
            print("警告：仓库文件数量过多，API 返回的列表可能不完整。")

        print("\nStep 2: 开始筛选文件...")
        # 遍历返回的整个文件树
        for item in data['tree']:
            # 'type' 为 'blob' 表示是文件，'tree' 表示是目录
            if item.get('type') == 'blob':
                file_path = item.get('path', '')
                file_name = os.path.basename(file_path) # 从路径中提取文件名

                # 应用筛选条件
                is_markdown = file_name.endswith('.md')
                #has_keyword = "行业" in file_name or "主题" in file_name                

                #if is_markdown and has_keyword:
                if is_markdown:
                    # 将文件的完整相对路径添加到列表中
                    file_list.append(file_path)
                    print(f"  [匹配成功] -> {file_path}")

    except requests.exceptions.RequestException as e:
        print(f"错误：请求 GitHub API 时发生网络错误: {e}")
        return []
    except requests.exceptions.HTTPError as e:
        print(f"错误：GitHub API 返回 HTTP 错误: {e.response.status_code}")
        print(f"响应内容: {e.response.text}")
        return []
    except KeyError as e:
        print(f"错误：解析 API 响应时出错，缺少键: {e}")
        return []

    print(f"\n筛选完毕，共找到 {len(file_list)} 个符合条件的文件待同步。")
    return file_list

def sync_github_to_oss():
    """
    主函数：将 GitHub 仓库中的文件同步到阿里云 OSS。
    """
    # === 第一步: 从 GitHub 获取文件列表 ===
    filelist = get_github_files_to_sync()

    if not filelist:
        print("\n文件列表为空，无需同步，程序结束。")
        return

    # === 第二步: 初始化 OSS Bucket ===
    try:
        print("\nStep 2: 连接到阿里云 OSS...")
        auth = oss2.Auth(OSS_ACCESS_KEY_ID, OSS_ACCESS_KEY_SECRET)
        bucket = oss2.Bucket(auth, OSS_ENDPOINT, OSS_BUCKET_NAME)
        print(f"成功连接到 Bucket: {OSS_BUCKET_NAME}")
    except Exception as e:
        print(f"错误：连接 OSS 失败: {e}")
        return

    # === 第三步: 遍历并同步文件 ===
    print(f"\nStep 3: 开始同步文件到 OSS 目录 '{OSS_TARGET_DIRECTORY}'...")
    uploaded_count = 0
    skipped_count = 0

    for filename in filelist:
        # 构造在 OSS 中的完整对象名 (路径 + 文件名)
        object_key = f"{OSS_TARGET_DIRECTORY}{filename}"

        try:
            # 检查文件是否已存在于 OSS
            if bucket.object_exists(object_key):
                print(f"-[SKIPPED]: 文件 '{filename}' 在 OSS 中已存在。")
                skipped_count += 1
                continue

            # 如果不存在，则从 GitHub 下载并上传
            print(f"+[PENDING]: 文件 '{filename}' 不存在，准备上传...")
            
            # 构造 GitHub raw 内容的下载链接
            download_url = f"https://raw.githubusercontent.com/{GITHUB_OWNER}/{GITHUB_REPO}/{GITHUB_BRANCH}/{filename}"
            
            # 下载文件内容 (注意要处理好URL中的中文字符等)
            # requests 会自动处理 URL 编码
            file_response = requests.get(download_url)
            file_response.raise_for_status() # 确保下载成功
            file_content = file_response.content # 使用 .content 获取二进制内容

            # 上传到 OSS
            bucket.put_object(object_key, file_content)
            print(f"  └─[SUCCESS]: 文件 '{filename}' 已成功上传到 OSS。")
            uploaded_count += 1

        except requests.exceptions.RequestException as e:
            print(f"  └─[ERROR]: 下载文件 '{filename}' 失败: {e}")
        except oss2.exceptions.OssError as e:
            print(f"  └─[ERROR]: 上传文件 '{filename}' 到 OSS 失败: {e}")
        except Exception as e:
            print(f"  └─[ERROR]: 处理文件 '{filename}' 时发生未知错误: {e}")

    # === 第四步: 打印总结报告 ===
    print("\n" + "="*30)
    print("同步任务完成！")
    print(f"总结: ")
    print(f"  成功上传: {uploaded_count} 个文件")
    print(f"  跳过(已存在): {skipped_count} 个文件")
    print(f"  总计处理: {len(filelist)} 个文件")
    print("="*30)


# --- 程序入口 ---
if __name__ == "__main__":
    sync_github_to_oss()
