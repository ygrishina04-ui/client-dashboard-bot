import os
import requests

BITRIX_WEBHOOK_URL = os.getenv("BITRIX_WEBHOOK_URL", "").rstrip("/")

if not BITRIX_WEBHOOK_URL:
    raise RuntimeError("Не задан BITRIX_WEBHOOK_URL")


def call_bitrix(method, params=None):
    url = f"{BITRIX_WEBHOOK_URL}/{method}.json"

    response = requests.post(
        url,
        json=params or {},
        timeout=30
    )

    response.raise_for_status()

    data = response.json()

    if "error" in data:
        raise RuntimeError(
            f"{data.get('error')}: "
            f"{data.get('error_description', '')}"
        )

    return data


def main():
    print("Проверяю подключение к Bitrix24...")

    result = call_bitrix(
        "crm.item.list",
        {
            "entityTypeId": 4,
            "select": [
                "id",
                "title"
            ],
            "order": {
                "id": "DESC"
            }
        }
    )

    items = result.get("result", {}).get("items", [])

    print(f"Получено компаний: {len(items)}")
    print()

    for company in items[:10]:
        print(
            f"ID={company.get('id')} | "
            f"{company.get('title')}"
        )


if __name__ == "__main__":
    main()
