import os, json, urllib.request
status = os.environ.get('STATUS', 'unknown')
sha = os.environ.get('SHA', '')[:7]
repo = os.environ.get('REPO', 'sauce')
emoji = '✅' if status == 'success' else '🔴'
text = '%s %s deploy: %s | commit: %s' % (emoji, repo, status, sha)
data = json.dumps({'chat_id': '312022420', 'text': text}).encode()
req = urllib.request.Request('https://api.telegram.org/bot' + os.environ['TG_TOKEN'] + '/sendMessage',
                             data=data, headers={'Content-Type': 'application/json'})
try:
    urllib.request.urlopen(req, timeout=10)
    print('notified')
except Exception as e:
    print('notify failed (non-blocking):', e)