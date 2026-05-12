import re

with open('app/api/routes.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Xóa các dòng import từ app.services.n8n_webhook_service
content = re.sub(r'from app\.services\.n8n_webhook_service import \([\s\S]*?\)\n', '', content)

# Remove forward_credentials_to_n8n and its route
content = re.sub(r'@router\.post\(\s*"/n8n/webhook-credentials"[\s\S]*?def forward_credentials_to_n8n[\s\S]*?(?=@router\.post|def _forward_json|def get_sheet_link_via_n8n|$)', '', content)

# Remove _forward_json_to_n8n_env_webhook
content = re.sub(r'def _forward_json_to_n8n_env_webhook[\s\S]*?(?=@router\.post|$)', '', content)

# Remove n8n_webhook_get_post_crawled
content = re.sub(r'@router\.post\(\s*"/n8n/webhook-get-post-crawled"[\s\S]*?def n8n_webhook_get_post_crawled[\s\S]*?(?=@router\.post|$)', '', content)

# Remove n8n_webhook_get_url_group_crawled
content = re.sub(r'@router\.post\(\s*"/n8n/webhook-get-url-group-crawled"[\s\S]*?def n8n_webhook_get_url_group_crawled[\s\S]*?(?=@router\.post|$)', '', content)

# Remove n8n_webhook_get_result_crawl_by_id
content = re.sub(r'@router\.post\(\s*"/n8n/webhook-get-result-crawl-by-id"[\s\S]*?def n8n_webhook_get_result_crawl_by_id[\s\S]*?(?=@router\.post|$)', '', content)

with open('app/api/routes.py', 'w', encoding='utf-8') as f:
    f.write(content)
print('Routes cleaned')
