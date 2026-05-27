import urllib.parse

# 1. ВСТАВЬ СЮДА ПОЛНУЮ ССЫЛКУ ИЗ ПЕРВОГО СURL (ДО ЗНАКА МЫШИ)
url_1 = "https://a201903291406004270013266.szwego.com/album/personal/all?&albumId=A201903291406004270013266&searchValue=&searchImg=&startDate=&endDate=&sourceId=&slipType=1&timestamp=1779260285179&requestDataType=&transLang=en"

# 2. ВСТАВЬ СЮДА ПОЛНУЮ ССЫЛКУ ИЗ ВТОРОГО СURL (КОГДА ПОДГРУЗИЛОСЬ ЕЩЕ 32 ТОВАРA)
url_2 = "https://a201903291406004270013266.szwego.com/album/personal/all?&albumId=A201903291406004270013266&searchValue=&searchImg=&startDate=&endDate=&sourceId=&slipType=1&timestamp=1779259988598&requestDataType=&transLang=en"

# Разбираем query-параметры
parsed_1 = urllib.parse.parse_qs(urllib.parse.urlparse(url_1).query)
parsed_2 = urllib.parse.parse_qs(urllib.parse.urlparse(url_2).query)

print("=== АНАЛИЗ ИЗМЕНЕНИЙ ПАРАМЕТРОВ ===")
all_keys = set(parsed_1.keys()).union(set(parsed_2.keys()))

for key in all_keys:
    val_1 = parsed_1.get(key, ["NOT FOUND"])[0]
    val_2 = parsed_2.get(key, ["NOT FOUND"])[0]
    
    if val_1 != val_2:
        print(f"Параметр [{key}]:")
        print(f"  В первом запросе: {val_1}")
        print(f"  Во втором запросе: {val_2}")

# Также проверим тело запроса (если ты заметил изменения в --data-raw)
# Вставь значения строк --data-raw сюда, если они отличаются:
data_raw_1 = "tagList=%5B%5D"
data_raw_2 = "tagList=%5B%5D"

if data_raw_1 != data_raw_2:
    print("\n=== ОТЛИЧИЯ В ТЕЛЕ (DATA-RAW) ===")
    print(f"Первый: {data_raw_1}")
    print(f"Второй: {data_raw_2}")
