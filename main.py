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
    
    # Timeout total de 3 segundos para as requisições
    timeout = aiohttp.ClientTimeout(total=3)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        # URLs e parâmetros para Insights da conta e Campanhas Ativas
        insights_url   = f"https://graph.facebook.com/v16.0/act_{account_id}/insights"
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
            logging.debug(f"Iniciando requisição GET para {url} com params: {params}")
            async with session.get(url, params=params) as resp:
                req_end = time.perf_counter()
                logging.debug(f"Requisição para {url} completed em {req_end - req_start:.3f}s status {resp.status}")
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
            logging.error(f"Erro durante as requisições: {e}")
            raise HTTPException(status_code=502, detail=f"Erro de conexão: {str(e)}")

        # Processa insights gerais
        if "data" not in insights_data or not insights_data["data"]:
            raise HTTPException(status_code=404, detail="Nenhum dado de insights encontrado.")
        insights_item = insights_data["data"][0]
        impressions = float(insights_item.get("impressions", 0) or 0)
        clicks      = float(insights_item.get("clicks", 0) or 0)
        ctr_value   = (clicks / impressions * 100) if impressions > 0 else 0
        ctr_formatted = format_percentage(ctr_value)
        cpc         = float(insights_item.get("cpc", 0) or 0)
        spent       = float(insights_item.get("spend", 0) or 0)

        conversions = 0.0
        for action in insights_item.get("actions", []):
            if action.get("action_type") == "offsite_conversion":
                try:
                    conversions += float(action.get("value", 0) or 0)
                except:
                    pass

        total_active_campaigns = len(campaigns_data.get("data", []))

        # Insights individuais por campanha
        async def get_campaign_insights(camp):
            cid = camp.get("id", "")
            obj = {
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
                    item = data["data"][0]
                    imp = float(item.get("impressions", 0) or 0)
                    clk = float(item.get("clicks", 0) or 0)
                    obj["impressions"] = int(imp)
                    obj["clicks"]      = int(clk)
                    obj["ctr"]         = format_percentage((clk / imp * 100) if imp > 0 else 0)
                    obj["cpc"]         = format_currency(float(item.get("cpc", 0) or 0))
            except Exception as e:
                logging.error(f"Erro insights campanha {cid}: {e}")
            return obj

        recent_campaignsGA = await asyncio.gather(
            *[get_campaign_insights(c) for c in campaigns_data.get("data", [])]
        )

        result = {
            "active_campaigns": total_active_campaigns,
            "impressions": int(impressions),
            "clicks": int(clicks),
            "ctr": ctr_formatted,
            "conversions": conversions,
            "average_cpc": format_currency(cpc),
            "roi": "0.00%",
            "total_spent": format_currency(spent),
            "recent_campaigns_total": len(recent_campaignsGA),
            "recent_campaignsGA": recent_campaignsGA
        }

        end_time = time.perf_counter()
        logging.debug(f"fetch_metrics concluído em {end_time - start_time:.3f}s")
        return result

@app.post("/metrics")
async def get_metrics(payload: dict = Body(...)):
    logging.debug("==== /metrics recebido ====")
    account_id   = payload.get("account_id")
    access_token = payload.get("access_token")
    if not account_id or not access_token:
        raise HTTPException(status_code=400, detail="É necessário fornecer 'account_id' e 'access_token'.")
    try:
        return await fetch_metrics(account_id, access_token)
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Erro no /metrics: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/pause_campaign")
async def pause_campaign(payload: dict = Body(...)):
    """
    Endpoint para pausar uma campanha do Meta Ads.
    Body deve conter: campaign_id, access_token
    """
    campaign_id  = payload.get("campaign_id")
    access_token = payload.get("access_token")
    if not campaign_id or not access_token:
        raise HTTPException(status_code=400, detail="Faltando 'campaign_id' ou 'access_token'.")

    url = f"https://graph.facebook.com/v16.0/{campaign_id}"
    params = {
        "status": "PAUSED",
        "access_token": access_token
    }

    timeout = aiohttp.ClientTimeout(total=3)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(url, data=params) as resp:
            text = await resp.text()
            if resp.status != 200:
                logging.error(f"Erro ao pausar {campaign_id}: {resp.status} {text}")
                raise HTTPException(status_code=resp.status, detail=text)
            logging.info(f"Campanha {campaign_id} pausada com sucesso")
            return {"success": True, "campaign_id": campaign_id}

if __name__ == "__main__":
    import uvicorn
    logging.info("Iniciando aplicação com uvicorn na porta 8080")
    uvicorn.run("main:app", host="0.0.0.0", port=8080, reload=True)
