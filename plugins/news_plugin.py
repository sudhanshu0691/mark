DECLARATION = {
    "name": "news_plugin",
    "description": "Provides daily basis news updates.",
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "description": "get_news"}
        },
        "required": ["action"]
    }
}

def run(args, ctx):
    return "[UNVERIFIED] News plugin created successfully."