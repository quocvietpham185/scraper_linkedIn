import re

with open('app/api/routes.py', 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Remove bad import
content = re.sub(r'from linkedin_group_crawler\.app\.services\.n8n_webhook_service import post_json_to_n8n_webhook\n?', '', content)

# 2. Add BaseModel import
if 'from pydantic import BaseModel' not in content:
    content = content.replace('from typing import Any', 'from typing import Any, Optional\nfrom pydantic import BaseModel')

# 3. Move from __future__ import annotations to the top
content = re.sub(r'from __future__ import annotations\n?', '', content)
content = '"""API routes for LinkedIn group crawler."""\nfrom __future__ import annotations\n' + content.replace('"""API routes for LinkedIn group crawler."""\n', '')

# 4. Remove _forward_n8n_group_webhook function
content = re.sub(r'def _forward_n8n_group_webhook[\s\S]*?(?=@router|$)', '', content)

with open('app/api/routes.py', 'w', encoding='utf-8') as f:
    f.write(content)
print('Routes fixed.')
