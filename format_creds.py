import json

# Read the Firebase credentials file
with open('tibby-teach-firebase-adminsdk-fbsvc-a51c5b7b7b.json', 'r') as f:
    creds = json.load(f)

# Convert to single line
single_line = json.dumps(creds, separators=(',', ':'))
print(single_line)
