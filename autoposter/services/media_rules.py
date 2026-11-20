"""Post-level constraints shared by planning, preflight and upload adapters."""

def combination_issue(platform, assets):
    if platform == 'instagram':
        from autoposter.services.image_edits import dimensions, instagram_ok
        for asset in assets:
            if getattr(asset, 'mime', '').startswith('image/') and hasattr(asset, 'width') and hasattr(asset, 'height'):
                if not instagram_ok(*dimensions(asset, 'instagram')):
                    return 'Instagram: Bildformat passt nicht. Im Bildeditor auf 3:4, 4:5, 1:1 oder 1,91:1 zuschneiden (nur Instagram oder alle Kanäle).'

        if not assets or len(assets) > 10:
            return 'Instagram: 1–10 Bilder oder ein einzelnes MP4-Video auswählen.'
        if len(assets) > 1 and any(getattr(a, 'mime', '') == 'video/mp4' for a in assets):
            return 'Instagram: Videos bitte einzeln als Reel planen; Bildkarussells dürfen bis zu 10 Bilder enthalten.'
    if platform == 'x'  and len(assets) > 1 and any(getattr(a, 'mime', '') in ('video/mp4', 'image/gif') for a in assets):
        return 'X: Ein Video oder animiertes GIF muss allein in einem Post stehen. Bilder und Videos bitte getrennt planen.'
    return None
