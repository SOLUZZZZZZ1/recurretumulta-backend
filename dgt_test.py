import requests

url = "https://prewww.dgt.es/WS_NTRA/consultaDEV"

headers = {
    "Content-Type": "text/xml;charset=UTF-8",
}

with open("consulta_dev_real_signed.xml", "r", encoding="utf-8") as f:
    xml_body = f.read()

response = requests.post(url, data=xml_body.encode("utf-8"), headers=headers)

print("STATUS:", response.status_code)
print("RESPONSE:")
print(response.text)