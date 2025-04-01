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
        # URL e parâmetros para Insights da conta
        insights_url = f"https://graph.facebook.com/v16.0/act_{account_id}/insights"
        params_insights = {
            "fields": "impressions,clicks,ctr,spend,cpc,actions",
            "date_preset": "maximum",
            "access_token": access_token
        }
        
        # URL e parâmetros para Campanhas Ativas
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
        
        # Função auxiliar para requisições GET
        async def fetch(url, params):
            req_start = time.perf_counter()
            logging.debug(f"Iniciando requisição GET para {url} com params: {params}")
            async with session.get(url, params=params) as resp:
                req_end = time.perf_counter()
                logging.debug(f"Requisição para {url} completada em {req_end - req_start:.3f} segundos com status {resp.status}")
                if resp.status != 200:
                    text = await resp.text()
                    raise Exception(f"Erro {resp.status}: {text}")
                return await resp.json()
        
        # Executa as requisições de insights e campanhas de forma paralela
        try:
            insights_data, campaigns_data = await asyncio.gather(
                fetch(insights_url, params_insights),
                fetch(campaigns_url, params_campaigns)
            )
        except Exception as e:
            logging.error(f"Erro durante as requisições: {e}")
            raise HTTPException(status_code=502, detail=f"Erro de conexão: {str(e)}")
        
        # Processa os dados gerais de insights da conta
        if "data" not in insights_data or not insights_data["data"]:
            raise HTTPException(status_code=404, detail="Nenhum dado de insights encontrado.")
        insights_item = insights_data["data"][0]
        
        try:
            impressions = float(insights_item.get("impressions", 0))
        except:
            impressions = 0.0
        try:
            clicks = float(insights_item.get("clicks", 0))
        except:
            clicks = 0.0
        # Calcula o CTR baseado em impressões e cliques (caso o dado não venha formatado)
        ctr_value = (clicks / impressions * 100) if impressions > 0 else 0.0
        ctr_formatted = format_percentage(ctr_value)
        try:
            cpc = float(insights_item.get("cpc", 0))
        except:
            cpc = 0.0
        try:
            spent = float(insights_item.get("spend", 0))
        except:
            spent = 0.0
        
        # Soma conversões (baseado em "offsite_conversion")
        conversions = 0.0
        for action in insights_item.get("actions", []):
            try:
                value = float(action.get("value", 0))
            except:
                value = 0.0
            if action.get("action_type") == "offsite_conversion":
                conversions += value
        
        total_active_campaigns = len(campaigns_data.get("data", []))
        
        # Função auxiliar para buscar insights de cada campanha individualmente
        async def get_campaign_insights(camp):
            campaign_id = camp.get("id", "")
            campaign_obj = {
                "id": campaign_id,
                "nome_da_campanha": camp.get("name", ""),
                "cpc": "0.00",
                "impressions": 0,
                "clicks": 0,
                "ctr": "0.00%"
            }
            campaign_insights_url = f"https://graph.facebook.com/v16.0/{campaign_id}/insights"
            params_campaign_insights = {
                "fields": "impressions,clicks,ctr,cpc",
                "date_preset": "maximum",
                "access_token": access_token
            }
            try:
                campaign_insights = await fetch(campaign_insights_url, params_campaign_insights)
                if "data" in campaign_insights and campaign_insights["data"]:
                    item = campaign_insights["data"][0]
                    impressions_camp = float(item.get("impressions", 0))
                    clicks_camp = float(item.get("clicks", 0))
                    ctr_val = (clicks_camp / impressions_camp * 100) if impressions_camp > 0 else 0.0
                    campaign_obj["impressions"] = int(impressions_camp)
                    campaign_obj["clicks"] = int(clicks_camp)
                    campaign_obj["ctr"] = format_percentage(ctr_val)
                    cpc_val = float(item.get("cpc", 0))
                    campaign_obj["cpc"] = format_currency(cpc_val)
            except Exception as e:
                logging.error(f"Erro ao buscar insights para campanha {campaign_id}: {e}")
            return campaign_obj
        
        # Busca insights individuais para cada campanha de forma concorrente
        campaigns_list = campaigns_data.get("data", [])
        tasks = [get_campaign_insights(camp) for camp in campaigns_list]
        recent_campaignsGA = await asyncio.gather(*tasks)
        
        recent_campaigns_total = len(recent_campaignsGA)
        logging.debug(f"Total de campanhas recentes processadas: {recent_campaigns_total}")
        logging.debug(f"Conteúdo de recent_campaignsGA: {recent_campaignsGA}")
        
        # Como não há dado de ROI no Facebook, definimos como "0.00%"
        roi_formatted = "0.00%"
        
        result = {
            "active_campaigns": total_active_campaigns,
            "impressions": int(impressions),
            "clicks": int(clicks),
            "ctr": ctr_formatted,
            "conversions": conversions,
            "average_cpc": format_currency(cpc),
            "roi": roi_formatted,
            "total_spent": format_currency(spent),
            "recent_campaigns_total": recent_campaigns_total,
            "recent_campaignsGA": recent_campaignsGA
        }
        end_time = time.perf_counter()
        logging.debug(f"fetch_metrics concluído em {end_time - start_time:.3f} segundos para account_id: {account_id}")
        return result

@app.post("/metrics")
async def get_metrics(payload: dict = Body(...)):
    logging.debug("==== Início da requisição para /metrics ====")
    try:
        payload_str = json.dumps(payload, indent=2, ensure_ascii=False)
    except Exception as e:
        payload_str = str(payload)
    logging.debug(f"Payload completo: {payload_str}")
    
    account_id = payload.get("account_id")
    access_token = payload.get("access_token")
    if not account_id or not access_token:
        logging.error("Payload inválido: 'account_id' ou 'access_token' ausentes.")
        raise HTTPException(status_code=400, detail="É necessário fornecer 'account_id' e 'access_token' no body.")
    
    try:
        result = await fetch_metrics(account_id, access_token)
    except Exception as e:
        logging.error(f"Erro no endpoint /metrics: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
    
    # Retorna a resposta em JSON formatada conforme o modelo do Google Ads
    return result

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8080, reload=True)
