import json
import logging
from contextlib import asynccontextmanager
from typing import cast

import openai
from botmailroom import BotMailRoom, EmailPayload, verify_webhook_signature
from exa_py import Exa
from fastapi import BackgroundTasks, Depends, FastAPI, Request, Response

from db_base import async_session_scope, db_setup, shutdown_session
from db_models import get_chat, store_chat
from settings import settings, setup_logging

setup_logging()
logger = logging.getLogger(__name__)

# Initialize clients
botmailroom_client = BotMailRoom(api_key=settings.botmailroom_api_key)
openai_client = openai.AsyncOpenAI(api_key=settings.openai_api_key)
exa_client = Exa(api_key=settings.exa_api_key)

# Initialize agent

tools = botmailroom_client.get_tools(
    tools_to_include=["botmailroom_send_email"]
) + [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Perform a search query on the web, and retrieve the most relevant URLs/web data.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query to perform.",
                    },
                },
                "required": ["query"],
            },
        },
    }
]
valid_tool_names = ",".join([tool["function"]["name"] for tool in tools])

system_prompt = f"""
Act as an executive assistant and respond on my behalf when appropriate. Follow these rules:
- Prioritize professionalism, clarity, and concise responses.
- The user is only able to respond to emails, so if you have a message to send, use the `botmailroom_send_email` tool.
- Email content should be formatted as email compliant html.
- When sending emails, prefer responding to an existing email thread over starting a new one.
- The sign-off name should be the name the user addresses you by in the email salutation or "Bot Mail" if they don't address you by name.
- Research Questions: If asked for research or factual data, use your internal knowledge and web searches (you can do multiple searches) to provide a comprehensive answer.
- Uncertainty: If the query is unclear or there's low confidence that you can add value, let the user know.
- Always respond with one of the following:
    - A tool call - the only valid tool names are {valid_tool_names}
    - `PLAN` followed by a description of the steps to complete the task
"""

# Initialize FastAPI app


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db_setup()
    yield
    await shutdown_session()


app = FastAPI(lifespan=lifespan)


@app.get("/healthz", include_in_schema=False)
def health():
    return {"status": "ok"}


def exa_search(query: str) -> str:
    response = exa_client.search_and_contents(
        query=query, type="auto", highlights=True
    )
    return "\n\n".join([str(result) for result in response.results])


async def handle_model_call(chat_id: str, chat_thread: list[dict]):
    cycle_count = 1
    while cycle_count <= settings.max_response_cycles:
        try:
            output = await openai_client.chat.completions.create(
                model="gpt-4o",
                messages=chat_thread,  # type: ignore
                tools=tools,  # type: ignore
            )
        except Exception as e:
            chat_thread.append(
                {
                    "role": "user",
                    "content": f"Error calling model: {e}",
                }
            )
            continue

        # execute the tool call if it exists
        if output.choices[0].message.tool_calls:
            chat_thread.append(output.choices[0].message.model_dump())
            tool_call = output.choices[0].message.tool_calls[0]
            logger.info(
                f"Tool call: {tool_call.function.name} with args: {tool_call.function.arguments}"
            )
            arguments = json.loads(tool_call.function.arguments)
            if tool_call.function.name.startswith("botmailroom_"):
                tool_output = botmailroom_client.execute_tool(
                    tool_call.function.name,
                    arguments,
                    enforce_str_output=True,
                )
                return
            elif tool_call.function.name.startswith("web_search"):
                tool_output = exa_search(arguments["query"])
            else:
                raise ValueError(f"Unknown tool: {tool_call.function.name}")
            chat_thread.append(
                {
                    "role": "tool",
                    "content": tool_output,
                    "tool_call_id": tool_call.id,
                }
            )
            logger.info(f"Tool output: {tool_output}")
        else:
            content = output.choices[0].message.content
            logger.warning(f"Invalid response from model: {content}")
            chat_thread.append(
                {
                    "role": "user",
                    "content": "Please respond with either a tool call or PLAN",
                }
            )

        async with async_session_scope() as session:
            await store_chat(chat_id, chat_thread, session)
        cycle_count += 1


async def handle_email(email_payload: EmailPayload):
    logger.info(f"Received email from {email_payload.from_address.address}")

    if (
        email_payload.previous_emails is not None
        and len(email_payload.previous_emails) > 0
    ):
        chat_id = email_payload.previous_emails[0].id
    else:
        chat_id = email_payload.id

    async with async_session_scope() as session:
        chat_model = await get_chat(chat_id, session)
        if chat_model is None:
            chat_thread = [{"role": "system", "content": system_prompt}]
        else:
            chat_thread = cast(list[dict], chat_model.chat)

    chat_thread.append(
        {"role": "user", "content": email_payload.thread_prompt}
    )
    await handle_model_call(chat_id, chat_thread)


async def _validate_and_parse_email(request: Request) -> EmailPayload:
    body = await request.body()
    payload = verify_webhook_signature(
        request.headers["X-Signature"],
        body,
        settings.botmailroom_webhook_secret,
    )
    return payload


@app.post("/receive-email", status_code=204)
async def receive_email(
    background_tasks: BackgroundTasks,
    email_payload: EmailPayload = Depends(_validate_and_parse_email),
):
    logger.info(email_payload)
    # move to background task to respond to webhook
    background_tasks.add_task(handle_email, email_payload)
    return Response(status_code=204)
