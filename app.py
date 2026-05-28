"""
Backend - Sincronizador Do Sports
"""
from flask import Flask, request, jsonify
from flask_cors import CORS
import pandas as pd
import requests
import json
import io
import os

app = Flask(__name__)
CORS(app)

CLIENT_ID     = "631781074725639"
CLIENT_SECRET = "a0AZOaipgt4Pbgn2myD1LRlQLg6IVuag"
USER_ID       = "525698506"

# Tokens en memoria (se renuevan solos)
_tokens = {
    "access_token":  os.environ.get("ACCESS_TOKEN", ""),
    "refresh_token": os.environ.get("REFRESH_TOKEN", "")
}

def renovar_token():
    r = requests.post(
        "https://api.mercadolibre.com/oauth/token",
        data={
            "grant_type":    "refresh_token",
            "client_id":     CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "refresh_token": _tokens["refresh_token"],
        }
    )
    if r.status_code == 200:
        data = r.json()
        _tokens["access_token"]  = data["access_token"]
        _tokens["refresh_token"] = data["refresh_token"]
        return True
    return False

def get_token():
    return _tokens["access_token"]

def es_error_token(texto):
    return texto and any(k in str(texto) for k in
                         ["invalid_token", "Malformed", "expired", "401", "unauthorized"])

def buscar_item_por_sku(sku):
    token = get_token()
    r = requests.get(
        f"https://api.mercadolibre.com/users/{USER_ID}/items/search",
        params={"seller_sku": sku},
        headers={"Authorization": f"Bearer {token}"}
    )
    if r.status_code == 401:
        if renovar_token():
            return buscar_item_por_sku(sku)
        return None
    if r.status_code == 200:
        resultados = r.json().get("results", [])
        return resultados[0] if resultados else None
    return None

def obtener_stock_ml(item_id):
    token = get_token()
    r = requests.get(
        f"https://api.mercadolibre.com/items/{item_id}",
        params={"attributes": "available_quantity"},
        headers={"Authorization": f"Bearer {token}"}
    )
    if r.status_code == 200:
        return r.json().get("available_quantity", None)
    return None

def actualizar_stock_ml(item_id, stock):
    token = get_token()
    r = requests.put(
        f"https://api.mercadolibre.com/items/{item_id}",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"available_quantity": stock}
    )
    if r.status_code == 200:
        return True, None
    if es_error_token(r.text):
        if renovar_token():
            return actualizar_stock_ml(item_id, stock)
    return False, r.text


@app.route("/")
def index():
    return "Do Sports Sync API OK"


@app.route("/sync", methods=["POST"])
def sync():
    if "file" not in request.files:
        return jsonify({"error": "No se recibió archivo"}), 400

    file = request.files["file"]
    df = pd.read_excel(io.BytesIO(file.read()))
    df.columns = df.columns.str.strip()

    # Construir dict SKU → stock
    df = df[df["SKU"].notna() & (df["SKU"].astype(str).str.strip() != "")]
    df["SKU"]   = df["SKU"].astype(str).str.strip()
    df["Stock"] = pd.to_numeric(df["Stock"], errors="coerce").fillna(0).astype(int)
    stock_map   = df.groupby("SKU")["Stock"].sum().to_dict()

    resultados = []

    for sku, stock in stock_map.items():
        item_id = buscar_item_por_sku(sku)

        if not item_id:
            resultados.append({"sku": sku, "status": "not_found", "stock": stock, "ml_id": "-"})
            continue

        stock_actual = obtener_stock_ml(item_id)

        if stock_actual is not None and stock_actual == stock:
            resultados.append({"sku": sku, "status": "no_change", "stock": stock, "ml_id": item_id})
            continue

        ok, error = actualizar_stock_ml(item_id, stock)

        if ok:
            resultados.append({"sku": sku, "status": "ok", "stock": stock,
                                "stock_anterior": stock_actual, "ml_id": item_id})
        else:
            resultados.append({"sku": sku, "status": "error", "stock": stock,
                                "ml_id": item_id, "error": str(error)})

    ok_count      = sum(1 for r in resultados if r["status"] == "ok")
    nochange_count = sum(1 for r in resultados if r["status"] == "no_change")
    warn_count    = sum(1 for r in resultados if r["status"] == "not_found")
    err_count     = sum(1 for r in resultados if r["status"] == "error")

    return jsonify({
        "results":   resultados,
        "summary": {
            "actualizados": ok_count,
            "sin_cambios":  nochange_count,
            "sin_publi_ml": warn_count,
            "errores":      err_count,
        }
    })


if __name__ == "__main__":
    app.run(debug=False)
