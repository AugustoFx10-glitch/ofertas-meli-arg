"""Actualiza precios, descuentos y stock de productos.json.
1) Intenta la API oficial de Mercado Libre (ML_CLIENT_ID / ML_CLIENT_SECRET desde GitHub Secrets).
2) Si la API no deja leer una publicacion, lee el precio desde el link de afiliado (meli.la).
Nunca imprime las claves ni el token. Siempre guarda el avance, aunque algo falle."""
import json, os, re, sys, time, urllib.request, urllib.parse, urllib.error
from datetime import datetime, timezone, timedelta

API = "https://api.mercadolibre.com"
UA = "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/129 Mobile Safari/537.36"
PRODUCTOS, IDS = "productos.json", "ids.json"
MINUTOS = float(os.environ.get("MINUTOS_MAX", "300"))      # tiempo maximo de lectura por corrida
INICIO = time.time()
AR = timezone(timedelta(hours=-3))
CARD = re.compile(r'"unique_id":')
META = re.compile(r'"metadata":\{"id":"(MLA\d+)"(?:,"product_id":"(MLA\d+)")?')
CUR = re.compile(r'"current_price":\{"value":([\d.]+)')
PREV = re.compile(r'"previous_price":\{"value":([\d.]+)')

def queda_tiempo():
    return (time.time() - INICIO) / 60 < MINUTOS

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
                time.sleep(4 * (i + 1)); continue
            raise
        except Exception:
            if i < tries - 1: time.sleep(3); continue
            raise

def token():
    cid, sec = os.environ.get("ML_CLIENT_ID"), os.environ.get("ML_CLIENT_SECRET")
    if not cid or not sec:
        print("AVISO: faltan los Secrets ML_CLIENT_ID / ML_CLIENT_SECRET. Sigo sin API."); return None
    body = urllib.parse.urlencode({"grant_type": "client_credentials", "client_id": cid, "client_secret": sec}).encode()
    try:
        _, txt = http(API + "/oauth/token", body, {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"})
        print("Token de Mercado Libre: OK")
        return json.loads(txt)["access_token"]
    except urllib.error.HTTPError as e:
        print(f"AVISO: Mercado Libre rechazo las credenciales (HTTP {e.code}). Sigo sin API."); return None

def code_of(link):
    return link.rstrip("/").rsplit("/", 1)[-1]

def num(x):
    return round(float(x)) if x else None

def leer_pagina(p, ref=None):
    """Lee el link de afiliado: identifica la publicacion y su precio actual."""
    final, html = http(p["link"])
    cards = [m.start() for m in CARD.finditer(html)] + [len(html)]
    tarjetas = []
    for a, b in zip(cards, cards[1:]):
        seg = html[a:b]
        m = META.search(seg)
        if not m: continue
        c, pv = CUR.search(seg), PREV.search(seg)
        tarjetas.append({"pos": a, "item": m.group(1), "product": m.group(2),
                         "price": num(c.group(1)) if c else None, "old": num(pv.group(1)) if pv else None})
    if not tarjetas: return None
    elegido = None
    if ref and ref.get("item"):
        elegido = next((t for t in tarjetas if t["item"] == ref["item"]), None)
        if not elegido: return {"item": ref["item"], "product": ref.get("product"), "price": None, "old": None}
    if not elegido:
        pic = re.search(r'-(MLA\d+)_', p.get("image", ""))
        if pic:
            for pos in [x.start() for x in re.finditer(pic.group(1), html)]:
                t = min(tarjetas, key=lambda t: abs(t["pos"] - pos))
                if abs(t["pos"] - pos) < 8000: elegido = t; break
    elegido = elegido or tarjetas[0]
    return elegido

def api_items(items, auth):
    datos, estado = {}, None
    for i in range(0, len(items), 20):
        if not queda_tiempo(): break
        url = API + "/items?ids=" + ",".join(items[i:i + 20]) + "&attributes=id,price,original_price,status,available_quantity"
        try:
            _, txt = http(url, headers=auth)
            for r in json.loads(txt):
                if r.get("code") == 200: datos[r["body"]["id"]] = r["body"]
                elif estado is None: estado = f"publicacion devolvio {r.get('code')}"
        except urllib.error.HTTPError as e:
            estado = f"HTTP {e.code}"
            if i == 0:
                print("AVISO: la API no deja leer publicaciones (" + estado + "). Uso los links de afiliado."); return {}
        except Exception as e:
            estado = type(e).__name__
        time.sleep(0.25)
    print("API: publicaciones leidas", len(datos), "| primer problema:", estado or "ninguno")
    return datos

def aplicar(p, precio, orig, activo, hoy):
    antes = (p.get("price"), p.get("oldPrice"), p.get("activo", True))
    if precio: p["price"] = precio
    if precio and orig and orig > precio:
        p["oldPrice"] = orig; p["discount"] = round((1 - precio / orig) * 100)
    elif precio:
        p.pop("oldPrice", None); p.pop("discount", None)
    if activo is not None: p["activo"] = activo
    if antes != (p.get("price"), p.get("oldPrice"), p.get("activo", True)):
        p["actualizado"] = hoy; return True
    return False

def guardar(productos, ids):
    json.dump(ids, open(IDS, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    with open(PRODUCTOS, "w", encoding="utf-8") as f:
        f.write(json.dumps(productos, ensure_ascii=False, separators=(",", ":")).replace("},{", "},\n{"))

def main():
    productos = json.load(open(PRODUCTOS, encoding="utf-8"))
    ids = json.load(open(IDS, encoding="utf-8")) if os.path.exists(IDS) else {}
    hoy = datetime.now(AR).strftime("%Y-%m-%d %H:%M")
    cambios = 0
    try:
        tk = token()
        datos = {}
        if tk:
            items = sorted({v["item"] for v in ids.values() if v and v.get("item")})
            datos = api_items(items, {"Authorization": "Bearer " + tk, "Accept": "application/json"}) if items else {}
        hechos = set()
        for p in productos:
            ref = ids.get(code_of(p["link"]))
            d = ref and datos.get(ref.get("item"))
            if d:
                activo = d.get("status") == "active" and (d.get("available_quantity") or 0) > 0
                cambios += aplicar(p, num(d.get("price")), num(d.get("original_price")), activo, hoy)
                ref["revisado"] = hoy; hechos.add(code_of(p["link"]))
        # Links de afiliado: primero los nunca revisados, despues los mas viejos
        pendientes = [p for p in productos if code_of(p["link"]) not in hechos]
        pendientes.sort(key=lambda p: (ids.get(code_of(p["link"])) or {}).get("revisado", ""))
        leidos = 0
        for p in pendientes:
            if not queda_tiempo(): break
            c = code_of(p["link"])
            try:
                t = leer_pagina(p, ids.get(c))
            except Exception as e:
                print("No se pudo leer", c, type(e).__name__); time.sleep(1); continue
            if t:
                ids[c] = {"item": t["item"], "product": t.get("product"), "revisado": hoy}
                if t.get("price"):
                    cambios += aplicar(p, t["price"], t.get("old"), True, hoy)
            leidos += 1
            if leidos % 200 == 0:
                guardar(productos, ids); print("Avance:", leidos, "links leidos")
            time.sleep(0.3)
        print("Links de afiliado leidos:", leidos, "| por API:", len(hechos))
    finally:
        guardar(productos, ids)
        print("Productos con cambios:", cambios, "| identificados:", len(ids), "de", len(productos))

if __name__ == "__main__":
    main()
