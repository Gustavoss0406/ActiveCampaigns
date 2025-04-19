import asyncio
import json
import time
import aiohttp
import logging
from fastapi import FastAPI, HTTPException, Body
from fastapi.middleware.cors import CORSMiddleware

# Configuração do logging para debug
logging.basicConfig(level=logging.DEBUG, format="%(asctime)s - %(levelname)s - %(message)s")

app = FastAPI()

# Habilita CORS para todas as origens (ajuste conforme necessário)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def format_percentage(value: float) -> str:
    """Formata um valor float como percentual com duas casas decimais."""
    return f"{value:.2f}%"

def format_currency(value: float) -> str:
    """Formata um valor float para string com duas casas decimais."""
    return f"{value:.2f}"

async def fetch_metrics(account_id: str, access_token: str):
    start_time = time.perf_counter()
    logging.debug(f"Iniciando fetch_metrics para account_id: {account_id}")

    timeout = aiohttp.ClientTimeout(total=3)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        insights_url = f"https://graph.facebook.com/v16.0/act_{account_id}/insights"
        params_insights = {
            "fields": "impressions,clicks,ctr,spend,cpc,actions",
            "date_preset": "maximum",
            "access_token": access_token
        }
        campaigns_url = f"https://graph.facebook.com/v16.0/act_{account_id}/campaigns"
        filtering = json.dumps([{
            "field": "effective_status",
            "operator": "IN",
            "value": ["ACTIVE"]
        }])
        params_campaigns = {
            "fields": "id,name,status",
            "filtering": filtering,
            "access_token": access_token
        }

        async def fetch(url, params):
            req_start = time.perf_counter()
            logging.debug(f"[fetch] GET {url} params={params}")
            async with session.get(url, params=params) as resp:
                elapsed = time.perf_counter() - req_start
                logging.debug(f"[fetch] {url} completed in {elapsed:.3f}s status {resp.status}")
                if resp.status != 200:
                    text = await resp.text()
                    raise Exception(f"Erro {resp.status}: {text}")
                return await resp.json()

        try:
            insights_data, campaigns_data = await asyncio.gather(
                fetch(insights_url, params_insights),
                fetch(campaigns_url, params_campaigns)
            )
        except Exception as e:
            logging.error(f"Erro durante requisições: {e}")
            raise HTTPException(status_code=502, detail=str(e))

        # insights gerais
        if not insights_data.get("data"):
            raise HTTPException(status_code=404, detail="Nenhum dado de insights encontrado.")
        item = insights_data["data"][0]
        impressions = float(item.get("impressions", 0) or 0)
        clicks      = float(item.get("clicks", 0) or 0)
        ctr_val     = (clicks / impressions * 100) if impressions > 0 else 0
        cpc         = float(item.get("cpc", 0) or 0)
        spent       = float(item.get("spend", 0) or 0)
        ctr_fmt     = format_percentage(ctr_val)

        conversions = sum(
            float(a.get("value", 0) or 0)
            for a in item.get("actions", [])
            if a.get("action_type") == "offsite_conversion"
        )

        total_active = len(campaigns_data.get("data", []))

        async def get_campaign_insights(camp):
            cid = camp.get("id")
            res = {
                "id": cid,
                "nome_da_campanha": camp.get("name", ""),
                "impressions": 0,
                "clicks": 0,
                "ctr": "0.00%",
                "cpc": "0.00"
            }
            url = f"https://graph.facebook.com/v16.0/{cid}/insights"
            params = {
                "fields": "impressions,clicks,ctr,cpc",
                "date_preset": "maximum",
                "access_token": access_token
            }
            try:
                data = await fetch(url, params)
                if data.get("data"):
                    itm = data["data"][0]
                    imp = float(itm.get("impressions", 0) or 0)
                    clk = float(itm.get("clicks", 0) or 0)
                    res["impressions"] = int(imp)
                    res["clicks"]      = int(clk)
                    res["ctr"]         = format_percentage((clk/imp*100) if imp>0 else 0)
                    res["cpc"]         = format_currency(float(itm.get("cpc",0) or 0))
            except Exception as e:
                logging.error(f"Erro insights {cid}: {e}")
            return res

        recent = await asyncio.gather(
            *[get_campaign_insights(c) for c in campaigns_data.get("data",[])]
        )

        result = {
            "active_campaigns": total_active,
            "impressions": int(impressions),
            "clicks": int(clicks),
            "ctr": ctr_fmt,
            "conversions": conversions,
            "average_cpc": format_currency(cpc),
            "roi": "0.00%",
            "total_spent": format_currency(spent),
            "recent_campaigns_total": len(recent),
            "recent_campaignsGA": recent
        }

        logging.debug(f"fetch_metrics concluído em {time.perf_counter() - start_time:.3f}s")
        return result

@app.post("/metrics")
async def get_metrics(payload: dict = Body(...)):
    account_id   = payload.get("account_id")
    access_token = payload.get("access_token")
    if not account_id or not access_token:
        raise HTTPException(status_code=400, detail="É necessário fornecer 'account_id' e 'access_token'.")
    return await fetch_metrics(account_id, access_token)

@app.post("/pause_campaign")
async def pause_campaign(payload: dict = Body(...)):
    """
    Pausa uma campanha do Meta Ads.
    Body: campaign_id, access_token
    """
    campaign_id  = payload.get("campaign_id")
    access_token = payload.get("access_token")
    if not campaign_id or not access_token:
        raise HTTPException(status_code=400, detail="Faltando 'campaign_id' ou 'access_token'.")

    url = f"https://graph.facebook.com/v16.0/{campaign_id}"
    params = {"status": "PAUSED", "access_token": access_token}
    timeout = aiohttp.ClientTimeout(total=3)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(url, data=params) as resp:
            text = await resp.text()
            if resp.status != 200:
                logging.error(f"Erro ao pausar {campaign_id}: {resp.status} {text}")
                raise HTTPException(status_code=resp.status, detail=text)
            logging.info(f"Campanha {campaign_id} pausada")
            return {"success": True, "campaign_id": campaign_id}

@app.post("/resume_campaign")
async def resume_campaign(payload: dict = Body(...)):
    """
    Reativa (coloca em ACTIVE) uma campanha pausada do Meta Ads.
    Body: campaign_id, access_token
    """
    campaign_id  = payload.get("campaign_id")
    access_token = payload.get("access_token")
    if not campaign_id or not access_token:
        raise HTTPException(status_code=400, detail="Faltando 'campaign_id' ou 'access_token'.")

    url = f"https://graph.facebook.com/v16.0/{campaign_id}"
    params = {"status": "ACTIVE", "access_token": access_token}
    timeout = aiohttp.ClientTimeout(total=3)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(url, data=params) as resp:
            text = await resp.text()
            if resp.status != 200:
                logging.error(f"Erro ao reativar {campaign_id}: {resp.status} {text}")
                raise HTTPException(status_code=resp.status, detail=text)
            logging.info(f"Campanha {campaign_id} reativada")
            return {"success": True, "campaign_id": campaign_id}

if __name__ == "__main__":
    import uvicorn
    logging.info("Iniciando app na porta 8080")
    uvicorn.run("main:app", host="0.0.0.0", port=8080, reload=True)
