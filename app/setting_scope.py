"""Explicit infrastructure scope. Account credentials and content never inherit."""
# Task names remain distinct: chat vision is NOT post vision.
GLOBAL_AI_KEYS = frozenset({
    'openrouter_api_key', 'vision_api_key', 'openrouter_model', 'classifier_model',
    'ppv_caption_model', 'vision_model', 'memory_model', 'temperature', 'max_tokens',
    'reasoning_mode', 'generation_retries', 'generation_retry_delay',
    'openrouter_model_caption', 'openrouter_model_text', 'openrouter_model_vision',
    'openrouter_model_fallback', 'openrouter_monthly_budget_usd',
})
# These fields are installation infrastructure, not the active creator/account.
GLOBAL_TECHNICAL_KEYS = frozenset({
    'openrouter_api_key', 'vision_api_key',
    'openrouter_model_caption', 'openrouter_model_text', 'openrouter_model_vision',
    'openrouter_model_fallback', 'openrouter_monthly_budget_usd',
    'public_base_url', 'comfy_url', 'comfy_model', 'comfy_qwen_model',
    'git_remote_url', 'git_branch', 'git_user_name', 'git_user_email',
    'github_token', 'git_commit_default',
    'update_check_enabled', 'update_check_interval_hours', 'update_notify_telegram',
    'update_state', 'telegram_enabled', 'telegram_bot_token', 'telegram_chat_id',
    'app_base_url', 'doku_markdown',
}) | GLOBAL_AI_KEYS


GLOBAL_TECHNICAL_SECRET_KEYS = frozenset({
    'openrouter_api_key', 'vision_api_key', 'github_token', 'telegram_bot_token',
})


def encode_technical_value(key, value):
    """Versioned envelope using the same installation key as AutoPost tokens."""
    if key in GLOBAL_TECHNICAL_SECRET_KEYS and value not in (None, ''):
        from autoposter.crypto import encrypt
        return {'format': 'technical-secret-v1', 'ciphertext': encrypt(str(value))}
    return value


def decode_technical_value(key, value):
    # Legacy plaintext remains readable until an explicit write/copy migration.
    # Never return an undecipherable envelope as if it were a usable API token.
    if key in GLOBAL_TECHNICAL_SECRET_KEYS and isinstance(value, dict):
        if value.get('format') != 'technical-secret-v1':
            raise ValueError('Unsupported technical secret format')
        from autoposter.crypto import decrypt
        return decrypt(value['ciphertext'])
    return value


def is_global_technical(key):
    return key in GLOBAL_TECHNICAL_KEYS
