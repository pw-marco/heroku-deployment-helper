import asyncio
from pyrogram import Client
from pyrogram.types import InlineQuery, InlineQueryResultArticle, InputTextMessageContent
from database.mongo_db import search_stolen_batches, is_premium_user

@Client.on_inline_query()
async def inline_search(client: Client, inline_query: InlineQuery):
    user_id = inline_query.from_user.id
    
    # Check Premium/Admin Access
    if not await is_premium_user(user_id):
        return await inline_query.answer(
            results=[
                InlineQueryResultArticle(
                    title="❌ Unauthorized Access",
                    description="You must be an Admin or Premium User to use inline search.",
                    input_message_content=InputTextMessageContent("❌ Unauthorized Access")
                )
            ],
            cache_time=0
        )
        
    query = inline_query.query.strip()
    
    if len(query) < 2:
        return await inline_query.answer(
            results=[
                InlineQueryResultArticle(
                    title="🔍 Search Stolen Batches",
                    description="Type at least 2 characters of the batch name to search.",
                    input_message_content=InputTextMessageContent("Type a batch name to search.")
                )
            ],
            cache_time=0
        )
        
    # Search DB
    results = await search_stolen_batches(query)
    
    inline_results = []
    
    for b in results:
        b_name = b.get("name", b.get("batch_name", "Unknown"))
        b_id = b.get("batch_id", "")
        
        inline_results.append(
            InlineQueryResultArticle(
                title=f"📚 {b_name}",
                description=f"ID: {b_id}",
                input_message_content=InputTextMessageContent(f"`{b_id}`")
            )
        )
        
    if not inline_results:
        inline_results.append(
            InlineQueryResultArticle(
                title="❌ No Batches Found",
                description="Try another search term.",
                input_message_content=InputTextMessageContent("No batches found.")
            )
        )
        
    await inline_query.answer(
        results=inline_results,
        cache_time=0,
        is_personal=True
    )
