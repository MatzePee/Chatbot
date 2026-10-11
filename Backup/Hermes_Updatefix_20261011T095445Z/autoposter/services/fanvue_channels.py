"""Two local publishing targets for the shared Fanvue account; no provider writes."""
import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from autoposter.models import Channel, ContentPlan, PostingPolicy
from . import chatbot_fanvue as shared

TARGETS = {'subscribers': 'Subscriber', 'followers-and-subscribers': 'Follower'}


def fixed_audience(channel):
    value = getattr(channel, 'fanvue_audience', '') or ''
    return value if channel.platform == 'fanvue' and value in TARGETS else ''


def audience_for(channel, requested=None):
    fixed = fixed_audience(channel)
    if fixed and requested and requested != fixed:
        raise ValueError(f'Dieser Kanal ist ausschließlich für {TARGETS[fixed]}-Posts vorgesehen.')
    return fixed or requested or channel.default_audience or 'subscribers'


async def ensure_channels(db):
    """Idempotent lazy setup, also after connecting an account without restarting.

    Existing channels, assignments and posts are deliberately not repurposed.
    Deterministic IDs and savepoints prevent duplicates during concurrent requests.
    """
    tokens = shared.chatbot_db.get_tokens()
    cfg = shared.configuration()
    if not tokens and not cfg['fanvue_client_id']:
        return []
    account = str(tokens['account_uuid'] or '') if tokens else ''
    handle = str(tokens['account_handle'] or '') if tokens else ''
    rows = list((await db.execute(select(Channel).where(
        Channel.platform == 'fanvue', Channel.fanvue_audience.in_(TARGETS)
    ).order_by(Channel.created_at, Channel.id))).scalars().all())
    # On disconnect keep the existing targets and their account binding.
    if not account and rows:
        account = rows[0].fanvue_account_uuid or ''
    result = []
    for audience, label in TARGETS.items():
        channel = next((c for c in rows if c.fanvue_audience == audience
                        and (c.fanvue_account_uuid or '') == account), None)
        if channel is None and account:
            # Targets created before the first OAuth login can acquire an identity.
            channel = next((c for c in rows if c.fanvue_audience == audience
                            and not c.fanvue_account_uuid), None)
            if channel:
                channel.fanvue_account_uuid = account
        if channel is None:
            channel_id = uuid.uuid5(uuid.NAMESPACE_URL, f'mp-creatorstudio/fanvue/{account or "pending"}/{audience}')
            try:
                async with db.begin_nested():
                    policy = PostingPolicy(free_post_ratio=1.0 if audience == 'followers-and-subscribers' else 0.0,
                                           auto_approve=False, max_images_per_post=20, text_only_ratio=0)
                    db.add(policy)
                    await db.flush()
                    channel = Channel(id=channel_id, platform='fanvue', display_name=f'Fanvue · {label}',
                                      handle=handle, default_audience=audience, fanvue_audience=audience,
                                      fanvue_account_uuid=account, policy_id=policy.id,
                                      color='#007aff' if audience == 'subscribers' else '#34a853',
                                      health='needs_reauth', health_note='Gemeinsame Fanvue-Verbindung')
                    db.add(channel)
                    await db.flush()
                    db.add(ContentPlan(channel_id=channel.id))
                    await db.flush()
            except IntegrityError:
                channel = await db.get(Channel, channel_id)
                if channel is None:
                    raise
        if handle and (not account or channel.fanvue_account_uuid == account):
            channel.handle = handle
        result.append(channel)
    await db.flush()
    return result
