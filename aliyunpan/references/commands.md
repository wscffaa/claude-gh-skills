# aliyunpan CLI 命令参考

基于 [tickstep/aliyunpan](https://github.com/tickstep/aliyunpan) 官方文档。

## 认证

```bash
# 登录（浏览器扫码完成两次登录）
aliyunpan login

# 查看当前登录账号
aliyunpan who

# 列出所有已登录账号
aliyunpan loglist

# 切换账号
aliyunpan su <uid>

# 退出登录
aliyunpan logout
```

**配置目录**：`export ALIYUNPAN_CONFIG_DIR=/path/to/config`

## 上传

```bash
aliyunpan upload <本地路径1> <本地路径2> ... <目标云盘目录>
aliyunpan u <本地路径1> ... <目标云盘目录>  # 简写
```

### 常用参数

| 参数 | 说明 |
|------|------|
| `-exn <regex>` | 排除匹配的文件（正则表达式） |

### 排除规则示例

```bash
# 排除 @eadir 文件夹
aliyunpan u -exn "^@eadir$" /local/path /cloud/path

# 排除点号开头的文件
aliyunpan u -exn "^\." /local/path /cloud/path

# 排除 Python 缓存
aliyunpan u -exn "^__pycache__$" -exn "\.pyc$" /local/path /cloud/path

# 排除 Git 目录
aliyunpan u -exn "^\.git$" /local/path /cloud/path
```

## 下载

```bash
aliyunpan download <云盘路径1> <云盘路径2> ...
aliyunpan d <云盘路径1> ...  # 简写
```

### 常用参数

| 参数 | 说明 |
|------|------|
| `--saveto <path>` | 指定保存目录 |
| `--ow` | 覆盖已存在文件 |
| `--skip` | 跳过同名文件（不检查 SHA1） |
| `-p <num>` | 下载线程数 |
| `-l <num>` | 同时下载文件数量 |
| `--retry <num>` | 失败重试次数（默认 3） |
| `--nocheck` | 不校验文件 |
| `--exn <regex>` | 排除文件（正则） |

### 示例

```bash
# 下载到指定目录
aliyunpan d --saveto /local/downloads /cloud/path

# 覆盖已存在文件
aliyunpan d --ow --saveto /local/downloads /cloud/path
```

## 同步

```bash
aliyunpan sync start -ldir "<本地目录>" -pdir "<云盘目录>" -mode "<模式>"
```

### 模式

| 模式 | 说明 |
|------|------|
| `upload` | 本地备份到云盘 |
| `download` | 云盘备份到本地 |

### 备份策略

| 策略 | 说明 |
|------|------|
| `exclusive` | 排他备份，目标目录多余文件会被删除 |
| `increment` | 增量备份，目标目录多余文件保留 |

### 示例

```bash
# 上传模式
aliyunpan sync start -ldir "/local/experiments" -pdir "/BasicOFR/experiments" -mode "upload"

# 下载模式
aliyunpan sync start -ldir "/local/experiments" -pdir "/BasicOFR/experiments" -mode "download"
```

## 文件管理

```bash
# 列出目录
aliyunpan ls <目录>
aliyunpan ll <目录>  # 详细列表

# 切换工作目录
aliyunpan cd <目录>

# 显示当前目录
aliyunpan pwd

# 创建目录
aliyunpan mkdir <目录名>

# 删除文件/目录
aliyunpan rm <路径1> <路径2> ...

# 移动文件
aliyunpan mv <源路径1> <源路径2> ... <目标目录>

# 重命名
aliyunpan rename <旧名> <新名>
```

## 配置

```bash
# 显示当前配置
aliyunpan config

# 设置下载保存目录
aliyunpan config set -savedir /path/to/download

# 设置下载并发数
aliyunpan config set -max_download_parallel 15

# 获取网盘配额
aliyunpan quota
```

## 调试

```bash
# 开启详细日志
export ALIYUNPAN_VERBOSE=1
```

## 注意事项

1. **路径区分大小写**：云盘路径严格区分大小写
2. **登录有效期**：登录 token 有过期时间，需定期重新登录
3. **网络稳定性**：大文件上传建议使用稳定网络
4. **排除规则**：使用正则表达式，注意转义特殊字符
