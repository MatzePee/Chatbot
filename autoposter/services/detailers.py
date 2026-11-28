"""Independent Impact Pack passes derived from FaceDetailer Krea.json and Detailer.json."""
import secrets

DEFAULT_DENOISE = {"face": .25, "hands": .35, "feet": .4}
DETECTORS = {"face": "bbox/face_yolov8m.pt", "hands": "bbox/hand_yolov8n.pt", "feet": "bbox/adetailerFootYolov8x_v20.pt"}
PROMPTS = {
    "face": "Detailed face, natural skin texture, sharp eyes, detailed iris and pupil, preserve identity and expression.",
    "hands": "Detailed hand, detailed fingers, detailed fingernails, XCYP Lady Hand, Lady Hand, natural skin texture, sharp focus.",
    "feet": "Detailed foot, detailed toes, detailed toenails, sharp focus.",
}


def workflow(part, photo, lora="", steps=3, denoise=None):
    def node(kind, **inputs):
        return {"class_type": kind, "inputs": inputs}

    face = part == "face"
    graph = {
        "1": node("LoadImage", image=photo),
        "2": node("UNETLoader", unet_name="sickOllie_krea2.safetensors" if face else "BigLoveKreaEdit3_bf16.safetensors", weight_dtype="default"),
        "3": node("CLIPLoader", clip_name="qwen3vl_4b_fp8_scaled.safetensors", type="krea2", device="default"),
        "5": node("VAELoader", vae_name="qwen_image_vae.safetensors"),
        "6": node("CLIPTextEncode", clip=["3", 0], text=PROMPTS[part]),
        "7": node("ConditioningZeroOut", conditioning=["6", 0]),
        "8": node("UltralyticsDetectorProvider", model_name=DETECTORS[part]),
        "9": node("SAMLoader", model_name="sam_vit_b_01ec64.pth", device_mode="AUTO"),
    }
    model = ["2", 0]
    if face:
        graph["12"] = node("ComfyUI-Krea2T-Enhancer", model=model, enabled=True, strength=1.5, debug=False)
        model = ["12", 0]
        graph["7"] = node("CLIPTextEncode", clip=["3", 0], text="blurry, smooth plastic skin, waxy, deformed, extra eyes, water")
        if lora.replace(chr(92), "/").split("/")[-1] == "SallyLarsen_KREA2Turbo_20260822_001.safetensors":
            graph["6"]["inputs"]["text"] = "sallylarsen, Dark eyes. 25 years old."
    if face and lora:
        graph["10"] = node("LoraLoaderModelOnly", model=model, lora_name=lora, strength_model=1.0)
        model = ["10", 0]
    graph["11"] = node("FaceDetailer", image=["1", 0], model=model, clip=["3", 0], vae=["5", 0],
        positive=["6", 0], negative=["7", 0], bbox_detector=["8", 0], sam_model_opt=["9", 0],
        guide_size=512 if face else 1808, guide_size_for=True, max_size=1024 if face else 1808, seed=secrets.randbits(48), steps=steps,
        cfg=1, sampler_name="euler" if face else "er_sde", scheduler="beta", denoise=DEFAULT_DENOISE[part] if denoise is None else denoise,
        feather=5, noise_mask=True, force_inpaint=face, bbox_threshold=.5 if face else .7,
        bbox_dilation={"face": 10, "hands": 70, "feet": 50}[part], bbox_crop_factor=3, sam_detection_hint="center-1",
        sam_dilation=0, sam_threshold=.93, sam_bbox_expansion=0, sam_mask_hint_threshold=.7,
        sam_mask_hint_use_negative="False", drop_size=10, wildcard="", cycle=1, inpaint_model=False,
        noise_mask_feather=20, tiled_encode=False, tiled_decode=False)
    graph["4"] = node("PreviewImage", images=["11", 0])
    return graph
