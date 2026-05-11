"""
内容过滤器模块
用于检测文本和图片中的敏感内容，包括辱华内容和NSFW内容
"""

import io
import logging
import re
from typing import Optional, Tuple

import requests
from PIL import Image

# 配置日志
logger = logging.getLogger(__name__)

# ==================== 敏感关键词定义 ====================

# 辱华相关关键词（历史否认、分裂主义等）
ANTI_CHINA_KEYWORDS = [
    # 历史否认
    "南京大屠杀是假的",
    "南京大屠杀不存在",
    "否认南京大屠杀",
    "日本侵华是假的",
    "慰安妇是假的",
    "否认慰安妇",
    "731部队是假的",
    "否认731",
    "侵略中国是假的",

    # 分裂主义
    "台独",
    "港独",
    "藏独",
    "疆独",
    "台湾独立",
    "香港独立",
    "西藏独立",
    "新疆独立",

    # 领土主权
    "中国侵略",
    "中国威胁论",
    "中国崩溃论",
]

# NSFW相关关键词（裸露/色情）
NSFW_KEYWORDS = [
    # 裸露相关
    "裸体",
    "全裸",
    "裸露",
    "色情",
    "淫秽",
    "av",
    "porn",
    "pornography",
    "nude",
    "naked",
    "sex",
    "sexy",
    "hentai",
    "ero",
    "ecchi",
    "r18",
    "r-18",
    "成人",
    "限制级",

    # 性行为相关
    "性交",
    "做爱",
    "性爱",
    "强奸",
    "乱伦",
    "sm",
    "捆绑",
    "调教",
]

# ==================== 文本内容检查 ====================

def check_text_content(text: str) -> Tuple[bool, str]:
    """检查文本是否包含敏感内容

    Args:
        text: 待检查的文本内容

    Returns:
        Tuple[bool, str]: (是否通过检查, 未通过的原因或空字符串)
    """
    if not text:
        return True, ""

    text_lower = text.lower()

    # 检查辱华关键词
    for keyword in ANTI_CHINA_KEYWORDS:
        if keyword.lower() in text_lower:
            logger.warning(f"检测到辱华内容: {keyword}")
            return False, f"包含辱华敏感词: {keyword}"

    # 检查NSFW关键词（短关键词使用单词边界匹配，避免误杀）
    for keyword in NSFW_KEYWORDS:
        keyword_lower = keyword.lower()
        # 短关键词（<=3字符）使用单词边界，避免误匹配（如"av"在"Kamui"中）
        if len(keyword) <= 3:
            pattern = r'\b' + re.escape(keyword_lower) + r'\b'
            if re.search(pattern, text_lower):
                logger.warning(f"检测到NSFW内容: {keyword}")
                return False, f"包含NSFW敏感词: {keyword}"
        else:
            if keyword_lower in text_lower:
                logger.warning(f"检测到NSFW内容: {keyword}")
                return False, f"包含NSFW敏感词: {keyword}"

    return True, ""

# ==================== 图片内容检查 ====================

def check_image_content(image_url: str) -> Tuple[bool, str]:
    """检查图片是否包含敏感内容（裸露等）

    使用简单的肤色检测作为启发式方法，或预留接口后续接入第三方API

    Args:
        image_url: 图片URL地址

    Returns:
        Tuple[bool, str]: (是否通过检查, 未通过的原因或空字符串)
    """
    if not image_url:
        return True, ""

    try:
        # 下载图片
        response = requests.get(image_url, timeout=30)
        response.raise_for_status()

        # 打开图片
        image = Image.open(io.BytesIO(response.content))

        # 转换为RGB模式（处理RGBA、P等模式）
        if image.mode != "RGB":
            image = image.convert("RGB")

        # 使用肤色检测作为启发式方法
        skin_ratio = _detect_skin_ratio(image)

        # 如果肤色比例过高，可能包含裸露内容
        if skin_ratio > 0.45:  # 阈值可根据实际情况调整
            logger.warning(f"检测到疑似NSFW图片，肤色比例: {skin_ratio:.2%}")
            return False, f"疑似包含裸露内容（肤色比例: {skin_ratio:.2%}）"

        return True, ""

    except requests.RequestException as e:
        logger.error(f"下载图片失败: {e}")
        return True, ""  # 下载失败时默认通过，避免误拦截
    except Exception as e:
        logger.error(f"图片检查失败: {e}")
        return True, ""  # 检查失败时默认通过

def _detect_skin_ratio(image: Image.Image) -> float:
    """检测图片中的肤色比例

    基于RGB颜色空间的简单肤色检测算法

    Args:
        image: PIL Image对象（RGB模式）

    Returns:
        float: 肤色像素占总像素的比例（0.0-1.0）
    """
    pixels = list(image.getdata())
    total_pixels = len(pixels)

    if total_pixels == 0:
        return 0.0

    skin_pixels = 0

    for r, g, b in pixels:
        # 简单的肤色检测规则
        # 肤色通常在RGB空间中满足：R > G > B 且 R > 95 且 G > 40 且 B > 20
        if (r > 95 and g > 40 and b > 20 and
            r > g and g > b and
            abs(r - g) > 15 and
            r > b and g > b):
            skin_pixels += 1

    return skin_pixels / total_pixels

# ==================== ContentFilter 类 ====================

class ContentFilter:
    """内容过滤器类

    提供文本和图片内容的敏感信息检测功能

    Attributes:
        anti_china_keywords: 辱华关键词列表
        nsfw_keywords: NSFW关键词列表
        skin_threshold: 肤色检测阈值（默认0.45）
    """

    def __init__(self, skin_threshold: float = 0.45):
        """初始化内容过滤器

        Args:
            skin_threshold: 肤色检测阈值，超过此值认为可能包含裸露内容
        """
        self.anti_china_keywords = ANTI_CHINA_KEYWORDS
        self.nsfw_keywords = NSFW_KEYWORDS
        self.skin_threshold = skin_threshold

    def check_text(self, text: str) -> Tuple[bool, str]:
        """检查文本内容

        Args:
            text: 待检查的文本

        Returns:
            Tuple[bool, str]: (是否通过, 原因)
        """
        return check_text_content(text)

    def check_image(self, image_url: str) -> Tuple[bool, str]:
        """检查图片内容

        Args:
            image_url: 图片URL

        Returns:
            Tuple[bool, str]: (是否通过, 原因)
        """
        return check_image_content(image_url)

    def add_anti_china_keyword(self, keyword: str) -> None:
        """添加辱华关键词

        Args:
            keyword: 要添加的关键词
        """
        if keyword not in self.anti_china_keywords:
            self.anti_china_keywords.append(keyword)
            logger.info(f"已添加辱华关键词: {keyword}")

    def add_nsfw_keyword(self, keyword: str) -> None:
        """添加NSFW关键词

        Args:
            keyword: 要添加的关键词
        """
        if keyword not in self.nsfw_keywords:
            self.nsfw_keywords.append(keyword)
            logger.info(f"已添加NSFW关键词: {keyword}")

# ==================== 第三方API接口（预留） ====================

class ThirdPartyContentAPI:
    """第三方内容审核API接口

    预留接口用于接入第三方内容审核服务，如：
    - 百度内容审核API
    - 阿里云内容安全API
    - 腾讯云天御内容安全API
    - Google Perspective API
    - AWS Rekognition
    """

    def __init__(self, api_key: Optional[str] = None, api_endpoint: Optional[str] = None):
        """初始化API客户端

        Args:
            api_key: API密钥
            api_endpoint: API端点地址
        """
        self.api_key = api_key
        self.api_endpoint = api_endpoint

    def check_text_api(self, text: str) -> Tuple[bool, str, dict]:
        """调用第三方API检查文本

        Args:
            text: 待检查的文本

        Returns:
            Tuple[bool, str, dict]: (是否通过, 原因, API原始响应)
        """
        # TODO: 实现具体的API调用逻辑
        logger.info("调用第三方文本审核API（预留接口）")
        return True, "", {}

    def check_image_api(self, image_url: str) -> Tuple[bool, str, dict]:
        """调用第三方API检查图片

        Args:
            image_url: 图片URL

        Returns:
            Tuple[bool, str, dict]: (是否通过, 原因, API原始响应)
        """
        # TODO: 实现具体的API调用逻辑
        logger.info("调用第三方图片审核API（预留接口）")
        return True, "", {}


# ==================== 主程序入口 ====================

if __name__ == "__main__":
    # 配置日志输出
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    # 创建过滤器实例
    filter_obj = ContentFilter()

    # 测试文本检查
    test_texts = [
        "这是一段正常的动漫资讯内容",
        "这部作品包含r18内容",
        "否认南京大屠杀的历史事实",
    ]

    print("=== 文本内容检查测试 ===")
    for text in test_texts:
        passed, reason = filter_obj.check_text(text)
        if passed:
            print(f"文本: {text[:20]}... -> 通过")
        else:
            print(f"文本: {text[:20]}... -> 拦截: {reason}")

    print("\n=== 模块加载完成 ===")
