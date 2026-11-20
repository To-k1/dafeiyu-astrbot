"""Owner-managed language guidance shared by FishHome's named roles."""

import json
import logging
from pathlib import Path, PureWindowsPath


FILE = "shared-language.json"
DEFAULT = "共同语言提示词_说话可以不急着说完_v1.0.md"
MAX_BYTES = 48 * 1024
logger = logging.getLogger(__name__)


def validate(filename, text):
    if (not isinstance(filename, str) or not filename or len(filename) > 180
            or filename != PureWindowsPath(filename).name or any(c in filename for c in ":/\\\r\n")
            or Path(filename).suffix.lower() not in {".txt", ".md"}):
        raise ValueError("请选择 TXT 或 Markdown 文件")
    if not isinstance(text, str) or not text.strip() or "\0" in text or len(text.encode("utf-8")) > MAX_BYTES:
        raise ValueError("提示词需要是非空 UTF-8 文本，最多 48 KiB")


def settings(folder):
    path = Path(folder) / FILE
    if not path.is_file():
        bundled = Path(__file__).with_name(DEFAULT)
        try:
            default_text = bundled.read_bytes().decode("utf-8-sig")
        except FileNotFoundError:
            return {"enabled": False, "filename": "", "text": ""}
        return {"enabled": True, "filename": DEFAULT, "text": default_text}
    result = json.loads(path.read_text(encoding="utf-8"))
    validate(result["filename"], result["text"])
    if type(result["enabled"]) is not bool:
        raise ValueError("共同语言开关无效")
    return result


def prompt(folder):
    try:
        config = settings(folder)
    except (OSError, KeyError, TypeError, ValueError):
        logger.warning("共同语言设置读取失败")
        return ""
    if not config["enabled"]:
        return ""
    return ("\n<fishhome_shared_language>\n"
            "以下是主人为具名角色设置的共同语言倾向。保留你自己的身份、人格与声音；"
            "任务的事实、交付格式、权限和工具规则继续适用。\n"
            + config["text"] + "\n</fishhome_shared_language>\n")
