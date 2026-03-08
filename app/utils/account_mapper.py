from typing import Optional

import structlog

logger = structlog.get_logger()

# Keyword-based category mapping rules
CATEGORY_RULES = {
    "交通與車輛": {
        "UBER": ["uber", "Uber"],
        "交通費": ["高鐵", "台鐵", "捷運", "客運", "火車"],
        "停車": ["停車"],
        "汽油": ["加油", "中油", "台塑"],
        "過路費": ["ETC", "遠通"],
    },
    "生活與起居": {
        "雜貨": ["全聯", "家樂福", "頂好", "美聯社", "7-11", "全家", "OK", "萊爾富"],
        "水電費": ["台電", "台灣自來水", "水費"],
        "電信帳單": ["中華電信", "遠傳", "台灣大"],
        "網路寬頻": ["中華電信HiNet", "凱擘"],
        "衣服": ["UNIQLO", "ZARA", "H&M", "GU"],
        "甜食/飲料": ["星巴克", "Starbucks", "路易莎", "LOUISA", "50嵐", "清心"],
        "食材": ["市場", "魚市"],
    },
    "娛樂與休閒": {
        "餐飲/外食": ["餐廳", "小吃", "麵", "飯", "鍋", "壽司"],
        "旅遊": ["飯店", "旅館", "民宿", "Booking", "Agoda"],
        "娛樂": ["電影", "KTV", "遊樂"],
        "訂閱服務": ["Netflix", "Spotify", "YouTube", "Disney", "Apple"],
    },
    "其他": {
        "好市多採買": ["COSTCO", "Costco", "好市多"],
        "悠遊卡加值": ["悠遊卡", "加值"],
        "應用程式": ["APPLE", "GOOGLE", "Apple", "Google Play"],
    },
}


def map_category(description: str) -> str:
    """Map a transaction description to a spending category."""
    desc_lower = description.lower()

    for group, categories in CATEGORY_RULES.items():
        for category_name, keywords in categories.items():
            for keyword in keywords:
                if keyword.lower() in desc_lower:
                    return category_name

    return "其他"


def get_destination_for_withdrawal(description: str) -> str:
    """Get the destination account (expense category) for a withdrawal."""
    return map_category(description)
