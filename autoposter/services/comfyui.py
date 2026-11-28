"""Server-side connection to the studio's ComfyUI installation."""
import asyncio
import io
import json
from pathlib import Path
import secrets
import uuid

import httpx
from fastapi import HTTPException, Response
from PIL import Image

from app import db
from . import detailers


def client():
    url = db.get_setting("comfy_url", "http://MatzesPC:8188")
    return httpx.AsyncClient(base_url=url, timeout=35, trust_env=False)


def check_png(raw, max_side=1024):
    try:
        with Image.open(io.BytesIO(raw)) as image:
            if image.format != "PNG" or max(image.size) > max_side:
                raise ValueError
            image.verify()
            return image.size
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise HTTPException(422, "Ungültiger Bildausschnitt.") from exc


def workflow(photo, selection, model):
    graph = {
        "1": {"class_type": "LoadImage", "inputs": {"image": photo}},
        "2": {"class_type": "LoadImageMask", "inputs": {"image": selection, "channel": "red"}},
        "3": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": model}},
        "5": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["3", 1], "text": "seamless continuation of the surrounding background, matching textures, natural photograph"}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["3", 1], "text": "new object, text, artifact, blurry patch"}},
        "7": {"class_type": "InpaintModelConditioning", "inputs": {"positive": ["5", 0], "negative": ["6", 0], "vae": ["3", 2], "pixels": ["1", 0], "mask": ["2", 0], "noise_mask": True}},
        "8": {"class_type": "KSampler", "inputs": {"model": ["3", 0], "positive": ["7", 0], "negative": ["7", 1], "latent_image": ["7", 2], "seed": secrets.randbits(48), "steps": 24, "cfg": 5.5, "sampler_name": "dpmpp_2m", "scheduler": "karras", "denoise": 1}},
        "9": {"class_type": "VAEDecode", "inputs": {"samples": ["8", 0], "vae": ["3", 2]}},
        "4": {"class_type": "PreviewImage", "inputs": {"images": ["9", 0]}},
    }
    return graph


QWEN_MODEL = "qwen_image_2.1_int8_convrot.safetensors"


def qwen_workflow(photo, selection, prompt):
    def node(kind, **inputs):
        return {"class_type": kind, "inputs": inputs}

    return {
        "1": node("LoadImage", image=photo),
        "2": node("LoadImageMask", image=selection, channel="red"),
        "3": node("UNETLoader", unet_name=db.get_setting("comfy_qwen_model", QWEN_MODEL), weight_dtype="default"),
        "5": node("CLIPLoader", clip_name="qwen3vl_8b_int8_convrot.safetensors", type="qwen_image", device="default"),
        "6": node("VAELoader", vae_name="qwen_image_2.1_vae_bf16.safetensors"),
        "7": node("TextEncodeQwenImage21", clip=["5", 0], vae=["6", 0], prompt=prompt, negative_prompt="",
                  resolution=0, **{"images.image_1": ["1", 0]}),
        "8": node("VAEEncodeForInpaint", pixels=["1", 0], vae=["6", 0], mask=["2", 0], grow_mask_by=0),
        "9": node("QwenImage21Cache", model=["3", 0], device="auto", dtype="default"),
        "10": node("KSampler", model=["9", 0], positive=["7", 0], negative=["7", 1], latent_image=["8", 0],
                   seed=secrets.randbits(48), steps=25, cfg=1.0, sampler_name="euler", scheduler="simple", denoise=1.0),
        "11": node("VAEDecode", samples=["10", 0], vae=["6", 0]),
        "4": node("PreviewImage", images=["11", 0]),
    }


OUTPAINT_PROMPT = "Extend the photograph seamlessly into the blank margins. Match perspective, light and texture."


REMOVE_PROMPT = ("Reconstruct the empty background where the object was removed. Continue the surrounding "
                 "surfaces, perspective, texture and lighting naturally. Remove all traces of the object "
                 "including its contact shadow. Preserve the surrounding subjects and their edges.")


def removal_workflow(photo, selection, model, detail=""):
    # Prefill with the dedicated inpaint model before Qwen sees the reference.
    # Keep the prefill as the starting latent for the lower-denoise finishing pass.
    prefill = workflow(photo, selection, model)
    graph = qwen_workflow(photo, selection, REMOVE_PROMPT + (" " + detail if detail else ""))
    graph["20"] = {"class_type": "GrowMask", "inputs": {"mask": ["2", 0], "expand": 8, "tapered_corners": True}}
    ids = {"1": "1", "2": "20", **{str(i): str(20+i) for i in (3, 5, 6, 7, 8, 9)}}
    for old in ("3", "5", "6", "7", "8", "9"):
        node = prefill[old]
        graph[ids[old]] = {"class_type": node["class_type"], "inputs": {
            name: [ids[value[0]], value[1]] if isinstance(value, list) else value
            for name, value in node["inputs"].items()}}
    graph["30"] = {"class_type": "ImageCompositeMasked", "inputs": {
        "destination": ["1", 0], "source": ["29", 0], "mask": ["20", 0], "x": 0, "y": 0, "resize_source": False}}
    graph["7"]["inputs"]["images.image_1"] = ["30", 0]
    graph["8"] = {"class_type": "VAEEncode", "inputs": {"pixels": ["30", 0], "vae": ["6", 0]}}
    graph["31"] = {"class_type": "SetLatentNoiseMask", "inputs": {"samples": ["8", 0], "mask": ["20", 0]}}
    graph["10"]["inputs"].update(latent_image=["31", 0], denoise=0.45)
    return graph


def full_image_workflow(photo, prompt):
    graph = qwen_workflow(photo, None, prompt)
    del graph["2"], graph["8"]
    graph["10"]["inputs"]["latent_image"] = ["7", 2]
    return graph


def krea_template():
    return json.loads(Path(__file__).with_name("krea_biglove_edit3.json").read_text(encoding="utf8"))


def krea_workflow(photo, selection, instruction):
    graph = krea_template()
    graph["72"]["inputs"]["image"] = photo
    graph["84"]["inputs"]["prompt"] = instruction
    # AutoPost supplies its white selection as a separate PNG instead of inverse alpha.
    graph["119"] = {"class_type": "LoadImageMask", "inputs": {"image": selection, "channel": "red"}}
    graph["111"]["inputs"]["mask"] = ["119", 0]
    final = graph.pop("29")
    graph["4"] = {"class_type": "PreviewImage", "inputs": {"images": final["inputs"]["images"]}}
    return graph


def downloadable_workflow(tool):
    """Expose the inference graph with image/mask preparation and compositing for the ComfyUI canvas."""
    if tool in detailers.DEFAULT_DENOISE:
        return detailers.workflow(tool, "bild.png", db.get_setting("comfy_character_lora", ""))
    if tool == "krea":
        return krea_template()
    if tool == "full":
        graph = full_image_workflow("bild.png", "Change only the shoes to red. Preserve their shape and all other details.")
    elif tool == "remove":
        graph = removal_workflow("bild.png", "maske.png", db.get_setting("comfy_model", "juggernautXL_versionXInpaint.safetensors"))
    elif tool == "erase":
        graph = workflow("bild.png", "maske.png", db.get_setting("comfy_model", "juggernautXL_versionXInpaint.safetensors"))
    else:
        prompt = OUTPAINT_PROMPT if tool == "outpaint" else "Replace the masked object with a red flower."
        graph = qwen_workflow("bild.png", "maske.png", prompt)
    photo, mask = ["1", 0], ["2", 0]
    if tool == "outpaint":
        graph["2"] = {"class_type": "ImagePadForOutpaint", "inputs": {
            "image": photo, "left": 128, "top": 0, "right": 128, "bottom": 0, "feathering": 0}}
        photo, mask = ["2", 0], ["2", 1]
        graph["7"]["inputs"]["images.image_1"] = photo
        graph["8"]["inputs"].update(pixels=photo, mask=mask)
    generated = graph["4"]["inputs"]["images"]
    if tool != "full":
        graph["12"] = {"class_type": "ImageCompositeMasked", "inputs": {
            "destination": photo, "source": generated, "mask": mask, "x": 0, "y": 0, "resize_source": True}}
        generated = ["12", 0]
    graph["4"] = {"class_type": "SaveImage", "inputs": {"images": generated, "filename_prefix": "AutoPost-" + tool}}

    output_types = {
        "GrowMask": ["MASK"], "SetLatentNoiseMask": ["LATENT"], "VAEEncode": ["LATENT"],
        "LoadImage": ["IMAGE", "MASK"], "LoadImageMask": ["MASK"], "ImagePadForOutpaint": ["IMAGE", "MASK"],
        "CheckpointLoaderSimple": ["MODEL", "CLIP", "VAE"], "UNETLoader": ["MODEL"], "CLIPLoader": ["CLIP"],
        "VAELoader": ["VAE"], "CLIPTextEncode": ["CONDITIONING"], "TextEncodeQwenImage21": ["CONDITIONING", "CONDITIONING", "LATENT"],
        "InpaintModelConditioning": ["CONDITIONING", "CONDITIONING", "LATENT"], "VAEEncodeForInpaint": ["LATENT"],
        "QwenImage21Cache": ["MODEL"], "KSampler": ["LATENT"], "VAEDecode": ["IMAGE"], "ImageCompositeMasked": ["IMAGE"], "SaveImage": [],
    }
    nodes, links, positions = {}, [], {}
    def place(key):
        if key in nodes:
            return
        entry = graph[key]
        kind = entry["class_type"]
        sockets, widgets, depth = [], [], 0
        for name, value in entry["inputs"].items():
            if isinstance(value, list):
                source, slot = value
                place(source)
                depth = max(depth, nodes[source]["pos"][0] // 420 + 1)
                output = nodes[source]["outputs"][slot]
                number = len(links) + 1
                links.append([number, int(source), slot, int(key), len(sockets), output["type"]])
                output["links"].append(number)
                sockets.append({"name": name, "type": output["type"], "link": number})
            else:
                widgets.append(value)
                if kind == "KSampler" and name == "seed":
                    widgets.append("randomize")
        if kind in ("LoadImage", "LoadImageMask"):
            widgets.append("image")
        row = positions.get(depth, 0)
        positions[depth] = row + 1
        nodes[key] = {"id": int(key), "type": kind, "pos": [depth * 420, row * 450], "size": [365, 370],
                      "flags": {}, "order": len(nodes), "mode": 0, "inputs": sockets,
                      "outputs": [{"name": t, "type": t, "links": []} for t in output_types[kind]],
                      "properties": {"Node name for S&R": kind}, "widgets_values": widgets}
    place("4")
    return {"version": 0.4, "last_node_id": max(map(int, nodes)), "last_link_id": len(links),
            "nodes": list(nodes.values()), "links": links, "groups": [], "config": {}, "extra": {}}


# Standard paths are relative to the ComfyUI installation. Model loaders also
# include files discovered through extra_model_paths.yaml.
MODEL_INPUTS = {
    "CheckpointLoaderSimple": ("ckpt_name", "checkpoints"),
    "UNETLoader": ("unet_name", "diffusion_models"),
    "CLIPLoader": ("clip_name", "text_encoders"),
    "VAELoader": ("vae_name", "vae"),
    "LoraLoader": ("lora_name", "loras"),
    "UltralyticsDetectorProvider": ("model_name", "ultralytics"),
    "SAMLoader": ("model_name", "sams"),
}
MODULE_PATHS = {
    "ComfyUI-Krea2T-Enhancer": "custom_nodes/ComfyUI-Krea2T-Enhancer/",
    "FaceDetailer": "custom_nodes/ComfyUI-Impact-Pack/",
    "SAMLoader": "custom_nodes/ComfyUI-Impact-Pack/",
    "UltralyticsDetectorProvider": "custom_nodes/ComfyUI-Impact-Subpack/",
    "Krea2EditModelPatch": "custom_nodes/comfyui-krea2edit/",
    "Krea2EditGroundedEncode": "custom_nodes/comfyui-krea2edit/",
    "TextEncodeQwenImage21": "comfy_extras/nodes_qwen.py",
    "QwenImage21Cache": "comfy_extras/nodes_qwen.py",
    "GrowMask": "comfy_extras/nodes_mask.py",
    "ImageCompositeMasked": "comfy_extras/nodes_mask.py",
    "GetImageSize": "comfy_extras/nodes_images.py",
    "EmptySD3LatentImage": "comfy_extras/nodes_sd3.py",
}


def choices(info, key):
    spec = (info or {}).get("input", {}).get("required", {}).get(key, [])
    if spec and isinstance(spec[0], list):
        return spec[0]
    if len(spec) > 1 and isinstance(spec[1], dict):
        return spec[1].get("options")
    return None


async def component_inventory(url, checkpoint, qwen_model, lora):
    graph = removal_workflow("photo", "mask", checkpoint)
    graph["3"]["inputs"]["unet_name"] = qwen_model
    nodes = list(graph.values()) + list(krea_template().values())
    for part in detailers.DEFAULT_DENOISE:
        nodes.extend(detailers.workflow(part, "photo", lora).values())
    kinds = sorted({n["class_type"] for n in nodes} | {"ImagePadForOutpaint", "LoraLoader"})
    async with httpx.AsyncClient(base_url=url, timeout=8, trust_env=False) as connection:
        async def inspect(kind):
            try:
                r = await connection.get("/object_info/" + kind)
                r.raise_for_status()
                obj = r.json()
                if isinstance(obj, dict):
                    return obj.get(kind, {})
            except (httpx.HTTPError, ValueError):
                pass
            return None
        metadata = dict(zip(kinds, await asyncio.gather(*(inspect(kind) for kind in kinds))))

    rows = []
    def add(group, name, path, present):
        rows.append(dict(group=group, name=name, path=path, present=present))

    required = {(n["class_type"], n["inputs"][MODEL_INPUTS[n["class_type"]][0]])
                for n in nodes if n["class_type"] in MODEL_INPUTS}
    if lora:
        required.add(("LoraLoader", lora))
    for kind, name in sorted(required):
        field, folder = MODEL_INPUTS[kind]
        options = choices(metadata[kind], field)
        add("Charakter-LoRA" if kind == "LoraLoader" else "Modell", name, f"models/{folder}/{name}",
            name in options if options is not None else None)
    for kind in kinds:
        if kind == "LoraLoader" and not lora:
            continue
        info = metadata[kind]
        path = MODULE_PATHS.get(kind, "nodes.py")
        module = (info or {}).get("python_module", "")
        if module.startswith("comfy_extras."):
            path = module.replace(".", "/") + ".py"
        elif module.startswith("custom_nodes."):
            path = "custom_nodes/" + module.split(".")[1] + "/"
        add("Knoten", kind, path, bool(info) if info is not None else None)
    # A loader can exist on older installations without the required architecture.
    for kind, field in [("KSampler", "sampler_name"), ("KSampler", "scheduler"), ("CLIPLoader", "type")]:
        options = choices(metadata[kind], field)
        for value in sorted({n["inputs"][field] for n in nodes if n["class_type"] == kind}):
            add("ComfyUI-Funktion", f"{kind} · {value}", "ComfyUI-Kern aktualisieren",
                value in options if options is not None else None)
    return {"rows": rows, "loras": choices(metadata["LoraLoader"], "lora_name") or []}


async def submit(photo, mask, instruction=None, *, remove=False, krea=False, detail=None, steps=3, denoise=None):
    max_side = 2048 if mask is None or krea else 1024
    size = check_png(photo, max_side)
    if mask is not None and size != check_png(mask, max_side):
        raise HTTPException(422, "Maske und Ausschnitt müssen gleich groß sein.")
    model = db.get_setting("comfy_model", "juggernautXL_versionXInpaint.safetensors")
    async with client() as connection:
        async def upload(raw):
            name = f"autopost-{uuid.uuid4().hex}.png"
            response = await connection.post("/upload/image", files={"image": (name, raw, "image/png")})
            return response.raise_for_status().json()["name"]
        try:
            if detail:
                graph = detailers.workflow(detail, await upload(photo), db.get_setting("comfy_character_lora", ""), steps, denoise)
            elif mask is None:
                graph = full_image_workflow(await upload(photo), instruction)
            else:
                image, selection = await asyncio.gather(upload(photo), upload(mask))
                if krea:
                    graph = krea_workflow(image, selection, instruction)
                elif remove:
                    graph = removal_workflow(image, selection, model, instruction)
                else:
                    graph = qwen_workflow(image, selection, instruction) if instruction else workflow(image, selection, model)
            response = await connection.post("/prompt", json={"prompt": graph})
            return str(uuid.UUID(response.raise_for_status().json()["prompt_id"]))
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            raise HTTPException(502, "ComfyUI-Auftrag fehlgeschlagen. Bitte LAN-Adresse und Inpainting-Checkpoint prüfen.") from exc


async def result(job):
    async with client() as connection:
        try:
            response = await connection.get("/history/" + job)
            record = response.raise_for_status().json().get(job, {})
            if record.get("status", {}).get("status_str") == "error":
                raise HTTPException(502, "ComfyUI konnte die Auswahl nicht bearbeiten.")
            images = record.get("outputs", {}).get("4", {}).get("images", [])
            if images:
                response = await connection.get("/view", params=images[0])
                return Response(response.raise_for_status().content, media_type="image/png", headers={"Cache-Control":"no-store"})
            return {"pending": True}
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            raise HTTPException(502, "Das Ergebnis von ComfyUI ist nicht erreichbar.") from exc
