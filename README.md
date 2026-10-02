# DOCX 文章语音生成

将 `New_Oriental_50.docx` 中的 50 篇文章，用你已有的 ElevenLabs 克隆声音分别生成 MP3。脚本使用 Python 标准库，无需安装 Python 依赖，也无需安装 skill。

原始资料可放在 `reference/` 下；默认 DOCX 会优先从这里读取，也兼容脚本旁的旧位置。

## 1. 预览拆分结果

需要 Python 3.10 或更新版本。在本目录执行：

```powershell
python docx_to_audio.py --dry-run
```

如果你的 Python 使用 `py` 启动，将命令中的 `python` 换成 `py -3`。

本机已在 `D:\miniconda\envs\elevenlabs\python.exe` 验证通过。也可直接使用这个解释器（以下步骤同样可以替换）：

```powershell
& 'D:\miniconda\envs\elevenlabs\python.exe' docx_to_audio.py --dry-run
```

预览会生成 `output/audio/json/articles.json` 和 `output/audio/text/` 下每篇文章的 UTF-8 文本，不会调用 API。核对文本中的文章边界后再生成音频。脚本按独立段落中的 `01  The Language of Music` 这类编号标题拆分，检查编号连续且恰好 50 篇；默认朗读标题和正文，不朗读标题编号。可用 `--skip-title` 只读正文。

## 2. 配置你的声音

在脚本旁边创建 `.env` 文件，以 UTF-8 编码保存，将占位内容替换为真实值：

```dotenv
ELEVENLABS_API_KEY=你的_API_Key
ELEVENLABS_VOICE_ID=你的克隆声音_Voice_ID
ELEVENLABS_MODEL_ID=eleven_multilingual_v2
```

支持空行、注释和单引号或双引号包裹的值。不进行变量替换或多行值解析。模型配置可省略，默认使用 `eleven_multilingual_v2`。也可用 `--env-file 路径` 指定其他配置文件。

### 朗读参数

`.env.example` 包含完整参数和中文说明，可在 `.env` 中调整：

```dotenv
ELEVENLABS_SPEED=1.0
ELEVENLABS_STABILITY=0.5
ELEVENLABS_SIMILARITY_BOOST=0.75
ELEVENLABS_STYLE=0.0
ELEVENLABS_USE_SPEAKER_BOOST=true
ELEVENLABS_TEXT_NORMALIZATION=auto
ELEVENLABS_OUTPUT_FORMAT=mp3_44100_128
ELEVENLABS_SEED=
```

`SPEED` 为语速倍数（0.25～4.0），其余三个数值型声音参数范围为 0～1；声音增强使用 `true` / `false`。以上是起始配置，可以先用 `0.9` 倍速试听。稳定性越高，表现越平稳；相似度越高，越贴近参考声音；风格强度越高，越夸张原声音的表达特点。[官方 skill 参数说明](https://github.com/elevenlabs/skills/blob/main/text-to-speech/references/voice-settings.md)

文本规范化支持 `auto`、`on`、`off`；种子留空表示随机，填整数时只尽力复现。输出仅支持 MP3 格式，以保持每篇一个 MP3 的流程；`mp3_44100_192` 需要 Creator 或更高套餐。`eleven_multilingual_v2` 不支持 `language_code`，因此未加入语言强制选项。[官方 API 说明](https://elevenlabs.io/docs/api-reference/text-to-speech/convert)

环境变量优先于 `.env`，预览模式也会验证并显示实际参数。参数会传入每次语音请求，并纳入缓存标识；调整参数后请使用新的 `--output` 目录。切换到 v4 时需要移除它不支持的语速、风格和声音增强配置。

仍可在同一个 PowerShell 窗口配置环境变量：

```powershell
$env:ELEVENLABS_API_KEY = '你的_API_Key'
$env:ELEVENLABS_VOICE_ID = '你的克隆声音_Voice_ID'
```

环境变量优先于 `.env`；若环境变量已设置为空，也不会回退到文件。API Key 不写入脚本或生成文件。`.env` 已在 `.gitignore` 中排除，不要把 Key 提交到代码仓库。

## 3. 先生成第一篇试听

```powershell
python docx_to_audio.py --start 1 --end 1
```

试听 `output/audio/mp3/01_The Language of Music.mp3`。使用的是 Voice ID 对应的声音，无需再次克隆。

## 4. 生成全部文章

```powershell
python docx_to_audio.py
```

每篇文章单独输出一个 MP3，例如 `01_The Language of Music.mp3`、`02_Schooling and Education.mp3`。Windows 文件名中的非法字符自动替换为下划线。

所有新输出统一放在脚本所在项目的 `output/` 下。`--output` 只指定其中的子文件夹名，默认是 `audio`；例如 `--output New_Oriental_30_slow` 对应 `output/New_Oriental_30_slow/`。无需在参数里再写 `output/`，也不能传绝对路径或包含路径分隔符的名称。各项目按文件类型分开保存：

```text
output/
  audio/
    text/     每篇文章的 .txt 文本
    json/     articles.json 和每篇音频的 .mp3.json 续跑记录
    mp3/      每篇文章的最终 .mp3 音频
    .cache/   分段合成缓存
```

项目根目录中已有的 `audio/`、`New_Oriental_30/` 等目录保持原位，仍可作为 `--articles-json` 的输入来源。若要复用旧音频，可以将整个项目输出目录（包括 `.cache` 和 JSON 记录）移入 `output/` 后续跑，要求语音参数一致。对于同一项目目录内的旧版平铺文件，脚本仍会自动按类型整理；若两个位置有同名文件，会停止并提示处理，避免覆盖。

默认模型为 `eleven_multilingual_v2`，输出为 `mp3_44100_128`。模型可通过 `.env` 中的 `ELEVENLABS_MODEL_ID` 或 `--model` 修改；优先级为 `--model` > 环境变量 `ELEVENLABS_MODEL_ID` > `.env` > 默认值。启动时会打印实际使用的模型，预览模式也会显示。请确认模型支持你的克隆声音及单次请求长度。默认每段最多 4500 字符。

```powershell
# 临时指定模型，生成第一篇到另一个目录
python docx_to_audio.py --model eleven_v3 --end 1 --output audio_v3
```

如果文章需要分段，先安装 FFmpeg 并确保 `ffmpeg -version` 可以运行。脚本会在任何 API 调用前检查 FFmpeg；只有一个分段的文章无需 FFmpeg。分段音频会用 FFmpeg 无重编码合并成一个 MP3。

## 断点续跑和其他用法

中断后重新运行相同命令：完成且配置一致的文章会跳过；已保存的分段会复用。`.cache` 及 `.mp3.json` 用于续跑，请保留。改变文本、Voice ID、模型或分段长度后，使用新的输出目录，以免覆盖已有音频。

```powershell
# 只生成第 10 到 15 篇
python docx_to_audio.py --start 10 --end 15

# 使用其他 DOCX 或输出目录
python docx_to_audio.py other.docx --expected-count 50 --output other_audio
```

遇到限流（429）或服务器错误（5xx）会有限重试；认证、权限、额度不足等错误会停止。网络中断不自动重发，因为服务端可能已经生成音频并消耗额度。重新运行可恢复已经落盘的结果；无法恢复尚未收到的响应。实际调用会按 ElevenLabs 的账户规则消耗额度。

实现依据：[官方快速入门](https://elevenlabs.io/docs/eleven-api/quickstart)、[语音生成 API](https://elevenlabs.io/docs/api-reference/text-to-speech/convert)、[模型及字符限制](https://elevenlabs.io/docs/overview/models)。

## 新东方生而为赢 PDF 的 30 篇英文文章

`extract_pdf_articles.py` 针对本项目的双语 PDF 提取英文标题及正文，跳过前两页目录、页脚页码和 `译文：` 后的全部内容，连接跨页句子，保留段落并检查恰好 30 篇。需要 Poppler 的 `pdftotext` 在 PATH 中，本机已经可用。该提取规则适用于这份 PDF，不是任意 PDF 的通用拆分规则。

```cmd
python extract_pdf_articles.py "reference/54305334-j-新东方生而为赢30篇美文背诵文本.pdf" --output New_Oriental_30
```

提取结果保存在 `output/New_Oriental_30/text/`，英文正文及来源页码保存在 `output/New_Oriental_30/json/extracted_articles.json`。原 PDF 中已有的英文拼写、语法和标点保留，不自动改写。

核对后使用相同 `.env` 生成 30 篇独立音频，保存到 `output/New_Oriental_30/mp3/`：

```cmd
python docx_to_audio.py --articles-json "output/New_Oriental_30/json/extracted_articles.json" --expected-count 30 --output New_Oriental_30
```

加 `--dry-run` 可仅预览；加 `--end 1` 可只读第一篇。JSON 输入会检查标题和正文不含中文后再调用 API。中断后重复相同命令继续生成。
