import base64

with open("tibby-teach-firebase-adminsdk-fbsvc-a51c5b7b7b.json", "r") as f:
    encoded = base64.b64encode(f.read().encode()).decode()

print(encoded)
