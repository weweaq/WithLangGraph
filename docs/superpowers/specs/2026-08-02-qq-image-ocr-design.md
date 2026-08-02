# QQ Image OCR Design

**Date**: 2026-08-02
**Status**: Approved
**Scope**: QQ frontend only (OCR tools registered as `@tool` for agent use)

## Problem

Users send images via QQ Official Bot, but the current `frontends/qq.py` only reads `data.content` (text). Images are silently ignored. Users need the agent to see and understand image content.

## Requirements

| # | Requirement |
|---|---|
| R1 | Always OCR + Agent: every image message triggers OCR then agent processing |
| R2 | Multi-image: OCR each image, merge with labels (图1/图2), feed to agent together |
| R3 | Silent degradation: any failure → reply "图片处理失败，请重试", no stack traces |
| R4 | Structured prefix: tell agent the image paths via structured prompt prefix |
| R5 | Optional user text: if user sent text with images, include it as "用户原文" |

## Architecture

### Data Flow

```
QQ user sends image(s) (+ optional text)
    ↓
qq.py on_message detects data.attachments / <img> tags
    ↓
Download each image to temp/qq_img_{n}.jpg (async httpx)
    ↓
Build prompt: "[用户发送了 N 张图片，已保存为 <paths>，请用 ocr_image 工具识别后处理] 用户原文：<text>"
    ↓
Pass to agent (graph.astream)
    ↓
Agent calls ocr_image(path) → gets {text, lines, details}
    ↓
Agent processes OCR text based on user's context/question
    ↓
Reply to QQ user
```

### File Changes

| File | Action | Details |
|---|---|---|
| `src/gacore/tools/ocr_tools.py` | New | `ocr_image(path: str)` and `ocr_screen(x1,y1,x2,y2)` as `@tool` |
| `src/gacore/tools/__init__.py` | Edit | Register new tools in `TOOL_NAMES` + `_TOOLS` |
| `src/gacore/frontends/qq.py` | Edit | Image detection, download, prompt building, error handling |
| `pyproject.toml` | Edit | Add `rapidocr-onnxruntime`, `Pillow`, `httpx` |

### Tool Schema

```python
@tool
def ocr_image(path: str) -> dict[str, Any]:
    """对本地图片文件做 OCR，返回识别文本和每个文本块的 bbox+置信度。
    支持中英文，~1s/次。当需要识别图片中的文字时使用。"""

@tool  
def ocr_screen(x1: int, y1: int, x2: int, y2: int) -> dict[str, Any]:
    """截取屏幕指定区域并 OCR。坐标为像素 (x1,y1,x2,y2)。
    当需要识别屏幕上某区域文字时使用。"""
```

Both return: `{"text": str, "lines": list[str], "details": [{"bbox": list, "text": str, "conf": float}]}`

### QQ Frontend Changes

1. **Image extraction**: `_extract_images(data)` → check `data.attachments` (CDN URLs) and `<img>` tags in content
2. **Download**: `_download_image(url, index)` → async httpx GET, save to `temp/qq_img_{index}.jpg`
3. **Prompt building**: `_build_image_prompt(paths, user_text)` → structured prefix with paths + user text
4. **Error handling**: wrap entire image pipeline in try/except → silent failure message

### Error Scenarios

| Scenario | Behavior |
|---|---|
| Image download fails | Reply "图片处理失败，请重试" |
| OCR returns empty text | Agent receives `{"text": "", ...}`, handles naturally |
| No images in message | Fall through to normal text processing (unchanged) |
| Multiple images, some fail | Process successful ones, ignore failed ones |

## Out of Scope

- CLI image input support
- Vision model (multimodal) understanding — text OCR only
- Screen OCR via QQ (no point — QQ is remote)
- Non-QQ frontend image support

## Testing Plan

1. Unit tests for `_extract_images`, `_download_image`, `_build_image_prompt`
2. Integration test: mock QQ message with image → verify prompt structure
3. Manual test: send real image via QQ → verify OCR + agent response
