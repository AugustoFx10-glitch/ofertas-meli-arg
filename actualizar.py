"""Actualiza precios, descuentos y stock de productos.json con la API de Mercado Libre.
Lee ML_CLIENT_ID y ML_CLIENT_SECRET de variables de entorno (GitHub Secrets).
Nunca imprime las claves ni el token."""
import json, os, re, sys, time, urllib.request, urllib.parse, urllib.error
from datetime import datetime, timezone, timedelta

API = "https://api.mercadolibre.com"
UA = "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/129 Mobile Safari/537.36"
PRODUCTOS, IDS = "productos.json", "ids.json"
MAX_RESOLVER = int(os.environ.get("MAX_RESOLVER", "1500"))  # links nuevos a identificar por corrida
META = re.compile(r'"metadata":\{"id":"(MLA\d+)"(?:,"product_id":"(MLA\d+)")?')

def http(url, data=None, headers=None, tries=3):
    h = {"User-Agent": UA, "Accept-Language": "es-AR"}
    h.update(headers or {})
    for i in range(tries):
        try:
            req = urllib.request.Request(url, data=data, headers=h)
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.geturl(), r.read().decode("utf-8", "ignore")
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and i < tries - 1:
                time.sleep(3 * (i + 1)); continue
            raise
        except Exception:
            if i < tries - 1: time.sleep(3); continue
            raise

def token():
    cid, sec = os.environ.get("ML_CLIENT_ID"), os.environ.get("ML_CLIENT_SECRET")
    if not cid or not sec:
        sys.exit("Faltan los Secrets ML_CLIENT_ID / ML_CLIENT_SECRET.")
    body = urllib.parse.urlencode({"grant_type": "client_credentials", "client_id": cid, "client_secret": sec}).encode()
    try:
        _, txt = http(API + "/oauth/token", body, {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"})
    except urllib.error.HTTPError as e:
        sys.exit(f"Mercado Libre rechazó las credenciales (HTTP {e.code}). Revisá los Secrets.")
    return json.loads(txt)["access_token"]

def code_of(link):
    return link.rstrip("/").rsplit("/", 1)[-1]

def resolver(p):
    """Identifica la publicación a partir del link de afiliado meli.la."""
    final, html = http(p["link"])
    m = re.search(r'MLA-?(\d{8,})', final)
    metas = [(x.start(), x.group(1), x.group(2)) for x in META.finditer(html)]
    pic = re.search(r'-(MLA\d+)_', p.get("image", ""))
    if pic and metas:
        for pos in [x.start() for x in re.finditer(pic.group(1), html)]:
            best = min(metas, key=lambda t: abs(t[0] - pos))
            if abs(best[0] - pos) < 6000:
                return {"item": best[1], "product": best[2]}
    if metas:
        return {"item": metas[0][1], "product": metas[0][2]}
    if m:
        return {"item": "MLA" + m.group(1), "product": None}
    return None

def main():
    productos = json.load(open(PRODUCTOS, encoding="utf-8"))
    ids = json.load(open(IDS, encoding="utf-8")) if os.path.exists(IDS) else {}
    tk = token()
    auth = {"Authorization": "Bearer " + tk, "Accept": "application/json"}

    # 1) identificar publicaciones nuevas
    nuevos = 0
    for p in productos:
        c = code_of(p["link"])
        if c in ids or nuevos >= MAX_RESOLVER: continue
        try:
            ids[c] = resolver(p)
        except Exception as e:
            print("No se pudo identificar", c, type(e).__name__)
            continue
        nuevos += 1
        time.sleep(0.4)
    json.dump(ids, open(IDS, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    print("Links identificados nuevos:", nuevos, "| total:", len(ids))

    # 2) consultar precios y stock de a 20
    items = sorted({v["item"] for v in ids.values() if v and v.get("item")})
    datos, errores = {}, 0
    for i in range(0, len(items), 20):
        lote = items[i:i + 20]
        url = API + "/items?ids=" + ",".join(lote) + "&attributes=id,price,original_price,status,available_quantity"
        try:
            _, txt = http(url, headers=auth)
            for r in json.loads(txt):
                if r.get("code") == 200: datos[r["body"]["id"]] = r["body"]
        except Exception as e:
            errores += 1
            print("Error en lote", i // 20, type(e).__name__)
        time.sleep(0.25)
    print("Publicaciones consultadas:", len(datos), "| lotes con error:", errores)
    if not datos:
        sys.exit("La API no devolvió datos; no se modifica nada.")

    # 3) actualizar productos.json
    hoy = datetime.now(timezone(timedelta(hours=-3))).strftime("%Y-%m-%d %H:%M")
    cambios = pausados = 0
    for p in productos:
        ref = ids.get(code_of(p["link"]))
        d = ref and datos.get(ref.get("item"))
        if not d: continue
        antes = (p.get("price"), p.get("oldPrice"), p.get("activo", True))
        precio, orig = d.get("price"), d.get("original_price")
        activo = d.get("status") == "active" and (d.get("available_quantity") or 0) > 0
        if precio: p["price"] = round(precio)
        if orig and precio and orig > precio:
            p["oldPrice"] = round(orig)
            p["discount"] = round((1 - precio / orig) * 100)
        else:
            p.pop("oldPrice", None); p.pop("discount", None)
        p["activo"] = activo
        if not activo: pausados += 1
        if antes != (p.get("price"), p.get("oldPrice"), p.get("activo", True)):
            cambios += 1
            p["actualizado"] = hoy
    with open(PRODUCTOS, "w", encoding="utf-8") as f:
        f.write(json.dumps(productos, ensure_ascii=False, separators=(",", ":")).replace("},{", "},\n{"))
    print("Productos con cambios:", cambios, "| sin stock o pausados:", pausados)

if __name__ == "__main__":
    main()
