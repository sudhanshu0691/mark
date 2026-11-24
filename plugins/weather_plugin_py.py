
import json

def run(args, ctx):
    city = args.get("city", "Mumbai")
    # Simulate weather lookup
    return f"Simulated weather for {city}: 25°C, Sunny."

DECLARATION = {
    "name": "weather_plugin",
    "description": "Provides daily weather updates.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "city": {
                "type": "STRING",
                "description": "City name"
            }
        }
    }
}
