import os
from dotenv import load_dotenv

print("Loading .env file...")
load_dotenv()

key = os.getenv("GROQ_API_KEY")
print("--- GROQ_API_KEY DEBUG ---")
print(f"Raw Value: {repr(key)}")
if key:
    print(f"Length: {len(key)}")
    has_r = '\r' in key
    print(f"Contains \\r (hidden carriage return): {has_r}")
    print(f"Ends with space: {key.endswith(' ')}")
else:
    print("Key is None")
print("--------------------------")
