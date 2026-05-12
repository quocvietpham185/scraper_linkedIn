import os

base_dir = r'f:\Nam_3\work-security-zone\minhhoang-linkedin-scraper\linkedin_group_crawler\app'
files_to_update = [
    os.path.join(base_dir, 'schemas', 'request_models.py'),
    os.path.join(base_dir, 'schemas', 'response_models.py'),
    os.path.join(base_dir, 'api', 'routes.py')
]

replacements = {
    'N8nWebhookNotifyResponse': 'StartCrawlResponse',
    'N8nWebhookNotifyData': 'StartCrawlData',
    'N8nGetAllGroupsRequest': 'GetAllGroupsRequest',
    'N8nAddGroupRequest': 'AddGroupRequest',
    'N8nRemoveGroupRequest': 'RemoveGroupRequest',
    'N8nUpdateGroupRequest': 'UpdateGroupRequest',
    'N8nGetSheetLinkRequest': 'GetSheetLinkRequest',
    'SheetLinkFromN8nResponse': 'GetSheetLinkResponse',
    'SheetLinkFromN8nData': 'GetSheetLinkData',
    'start_n8n_workflow': 'start_crawl_workflow',
    'n8n_groups_get_all': 'groups_get_all',
    'n8n_groups_add': 'groups_add',
    'n8n_groups_remove': 'groups_remove',
    'n8n_groups_update': 'groups_update',
    'get_sheet_link_via_n8n': 'get_sheet_link'
}

for file_path in files_to_update:
    if os.path.exists(file_path):
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        for old, new in replacements.items():
            content = content.replace(old, new)
            
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)
            
print('Done replacing n8n traces in models and routes.')
