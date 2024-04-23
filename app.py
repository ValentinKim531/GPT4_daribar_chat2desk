from fastapi import FastAPI, Request
from openai import AsyncOpenAI
import httpx
import asyncio
from environs import Env
import logging
import json
import re

logging.basicConfig(level=logging.INFO)

app = FastAPI()
env = Env()
env.read_env()

CHAT2DESK_API_URL = "https://api.chat2desk.com/v1/messages"
CHAT2DESK_CLIENTS_URL = "https://api.chat2desk.com/v1/clients"
CHAT2DESK_TOKEN = env.str("CHAT2DESK_TOKEN")
OPENAI_API_KEY = env.str("OPENAI_API_KEY")
ASSISTANT_ID = env.str("ASSISTANT_ID")

client = AsyncOpenAI(api_key=OPENAI_API_KEY)

@app.on_event("startup")
async def startup_event():
    bot_token = env.str("BOT_TOKEN")
    webhook_url = "https://gpt4daribarchat2desk-production.up.railway.app/receive-message/"
    async with httpx.AsyncClient() as client:
        await client.post(f"https://api.telegram.org/bot{bot_token}/deleteWebhook?drop_pending_updates=True")
        await client.post(f"https://api.telegram.org/bot{bot_token}/setWebhook?url={webhook_url}")


def remove_annotations(text: str) -> str:
    pattern = r'\【.*?\】'
    cleaned_text = re.sub(pattern, '', text)
    return cleaned_text


async def get_or_create_client(chat_id):
    headers = {
        "Authorization": f"{CHAT2DESK_TOKEN}",
        "Content-Type": "application/json"
    }
    json_data = {
        "phone": f"{chat_id}",
        "transport": "telegram"
    }
    async with httpx.AsyncClient() as http_client:
        response = await http_client.post(CHAT2DESK_CLIENTS_URL, headers=headers, json=json_data)
        if response.status_code == 200:
            return response.json()['data']['id']
        elif response.status_code == 400 and "already exist" in response.text:
            # Извлекаем client_id из ответа, если клиент уже существует
            return json.loads(response.text)['errors']['client'][1].split(':')[1]
        else:
            logging.error(f"Failed to create or find the client in Chat2Desk: {response.text}")
            return None


@app.post("/receive-message/")
async def receive_message(request: Request):
    data = await request.json()
    chat_id = data['message']['chat']['id']
    user_message = data['message'].get('text', 'No text provided')
    logging.info(f"Received message from Telegram chat ID {chat_id}: '{user_message}'")

    client_id = await get_or_create_client(chat_id)
    if not client_id:
        return {"status": "error", "message": "Could not identify or create client in Chat2Desk"}

    thread = await client.beta.threads.create()
    await client.beta.threads.messages.create(thread_id=thread.id, role="user", content=user_message)

    run = await client.beta.threads.runs.create(thread_id=thread.id, assistant_id=ASSISTANT_ID)
    while run.status in ['queued', 'in_progress', 'cancelling']:
        await asyncio.sleep(1)
        run = await client.beta.threads.runs.retrieve(thread_id=thread.id, run_id=run.id)

    if run.status == 'completed':
        messages = await client.beta.threads.messages.list(thread_id=thread.id)
        assistant_message = ' '.join([remove_annotations(msg.content[0].text.value) for msg in messages.data if msg.role == 'assistant'])
        logging.info(f"Answer from Assistant: '{assistant_message}'")

        headers = {"Authorization": f"{CHAT2DESK_TOKEN}"}
        async with httpx.AsyncClient() as http_client:
            response = await http_client.post(
                f"{CHAT2DESK_API_URL}?text={assistant_message}&client_id={client_id}&transport=telegram",
                headers=headers)
            if response.status_code == 200:
                logging.info(f"Message successfully sent to chat ID {chat_id}: {assistant_message}")
            else:
                logging.error(f"Failed to send message to chat ID {chat_id}: {response.status_code} {response.text}")
    else:
        logging.error("Failed to get a response from the assistant")
        assistant_message = "Unable to get a response from the assistant."

    return {"status": "sent", "response": assistant_message}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
