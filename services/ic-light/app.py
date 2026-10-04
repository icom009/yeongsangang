"""IC-Light(배경 맞춤 재조명) 서비스. 집 GPU 서버에서만 따로 돌린다 (선택 기능).

부스 서버(booth/ai.py)가 인물(알파 포함)과 배경을 보내면, 그 장면의 빛으로 다시 조명한
사진을 돌려준다. 부스는 그 결과에서 '빛'만 가져다 쓰므로 얼굴은 바뀌지 않는다.
모델을 올리는 데 몇 분 걸리고, 준비되기 전에는 /health가 503이라 부스는 그냥 AI 없이 돈다.

구현은 공식 데모(lllyasviel/IC-Light gradio_demo_bg.py)를 따랐다.
UNet 첫 층을 4채널 → 12채널(잡음 + 인물 잠재 + 배경 잠재)로 늘리고 가중치 차이를 더해 쓴다.
"""
import base64
import contextlib
import io
import os
import threading

import numpy as np
import torch
from diffusers import AutoencoderKL, DPMSolverMultistepScheduler, StableDiffusionPipeline, UNet2DConditionModel
from fastapi import FastAPI, HTTPException
from huggingface_hub import hf_hub_download
from PIL import Image
from pydantic import BaseModel, Field
from safetensors.torch import load_file
from transformers import CLIPTextModel, CLIPTokenizer

BASE = os.environ.get('IC_BASE', 'stablediffusionapi/realistic-vision-v51')
WEIGHT = os.environ.get('IC_WEIGHT', 'iclight_sd15_fbc.safetensors')  # fbc: 배경 맞춤
STEPS = int(os.environ.get('IC_STEPS', 20))
CFG = float(os.environ.get('IC_CFG', 2.0))
DEVICE = 'cuda' if torch.cuda.is_available() else 'cpu'

_pipe = None
_vae = None
_ready = False
_gpu = threading.Lock()  # GPU는 한 장씩 (부스 매팅과 메모리를 나눠 쓴다)


def _patch_unet(unet):
    """첫 층을 12채널로 늘리고(잡음 4 + 인물 4 + 배경 4) IC-Light 가중치 차이를 더한다."""
    with torch.no_grad():
        conv = torch.nn.Conv2d(12, unet.conv_in.out_channels, unet.conv_in.kernel_size,
                               unet.conv_in.stride, unet.conv_in.padding)
        conv.weight.zero_()
        conv.weight[:, :4].copy_(unet.conv_in.weight)
        conv.bias = unet.conv_in.bias
        unet.conv_in = conv
    offset = load_file(hf_hub_download('lllyasviel/ic-light', WEIGHT))
    origin = unet.state_dict()
    unet.load_state_dict({k: origin[k] + offset[k] for k in origin}, strict=True)
    return unet


_forward = UNet2DConditionModel.forward


def _hooked_forward(self, sample, timestep, encoder_hidden_states, **kwargs):
    conds = kwargs['cross_attention_kwargs']['concat_conds'].to(sample)
    conds = torch.cat([conds] * (sample.shape[0] // conds.shape[0]), dim=0)
    kwargs['cross_attention_kwargs'] = {}
    return _forward(self, torch.cat([sample, conds], dim=1), timestep, encoder_hidden_states, **kwargs)


UNet2DConditionModel.forward = _hooked_forward


def load():
    global _pipe, _vae, _ready
    tokenizer = CLIPTokenizer.from_pretrained(BASE, subfolder='tokenizer')
    text_encoder = CLIPTextModel.from_pretrained(BASE, subfolder='text_encoder')
    vae = AutoencoderKL.from_pretrained(BASE, subfolder='vae')
    unet = _patch_unet(UNet2DConditionModel.from_pretrained(BASE, subfolder='unet'))

    text_encoder = text_encoder.to(DEVICE, dtype=torch.float16)
    unet = unet.to(DEVICE, dtype=torch.float16)
    vae = vae.to(DEVICE, dtype=torch.bfloat16)  # fp16 VAE는 가끔 깨진 색이 나온다

    scheduler = DPMSolverMultistepScheduler(
        num_train_timesteps=1000, beta_start=0.00085, beta_end=0.012, beta_schedule='scaled_linear',
        algorithm_type='sde-dpmsolver++', use_karras_sigmas=True, steps_offset=1)
    _pipe = StableDiffusionPipeline(
        vae=vae, text_encoder=text_encoder, tokenizer=tokenizer, unet=unet, scheduler=scheduler,
        safety_checker=None, feature_extractor=None, requires_safety_checker=False)
    _pipe.set_progress_bar_config(disable=True)
    _vae = vae
    _ready = True
    print(f'[IC-Light] 준비 완료 ({DEVICE}, {WEIGHT})', flush=True)


@contextlib.asynccontextmanager
async def lifespan(_):
    # 모델 내려받기·올리기는 몇 분 걸린다. 그동안에도 /health가 대답해야 하므로 따로 올린다
    threading.Thread(target=load, daemon=True).start()
    yield


app = FastAPI(title='IC-Light 재조명', lifespan=lifespan, docs_url=None, redoc_url=None)


@app.get('/health')
def health():
    if not _ready:
        raise HTTPException(503, 'loading')
    return {'ok': True, 'device': DEVICE}


class Req(BaseModel):
    fg: str                      # 인물 PNG(RGBA) base64
    bg: str                      # 배경 JPEG base64
    prompt: str = ''
    negative: str = ''
    width: int = Field(1024, ge=256, le=1536)
    height: int = Field(768, ge=256, le=1536)
    steps: int = Field(0, ge=0, le=60)
    cfg: float = 0.0
    seed: int = 12345


def _decode(b64):
    return Image.open(io.BytesIO(base64.b64decode(b64)))


def _to_latent(arrays):
    x = np.stack(arrays).astype(np.float32) / 127.5 - 1.0
    x = torch.from_numpy(x).permute(0, 3, 1, 2).to(DEVICE, dtype=_vae.dtype)
    return _vae.encode(x).latent_dist.mode() * _vae.config.scaling_factor


@torch.inference_mode()
def _relight(req):
    w, h = req.width // 8 * 8, req.height // 8 * 8
    fg = np.array(_decode(req.fg).convert('RGBA').resize((w, h), Image.LANCZOS), np.float32)
    bg = np.array(_decode(req.bg).convert('RGB').resize((w, h), Image.LANCZOS), np.float32)
    # 인물만 남기고 나머지는 중간 회색 (공식 데모와 같은 입력 형태)
    a = fg[..., 3:4] / 255
    person = 127 + (fg[..., :3] - 127) * a

    conds = _to_latent([person, bg])
    conds = torch.cat([c[None] for c in conds], dim=1).to(dtype=torch.float16)
    latents = _pipe(
        prompt=req.prompt or 'natural light', negative_prompt=req.negative,
        width=w, height=h, num_inference_steps=req.steps or STEPS,
        guidance_scale=req.cfg or CFG, output_type='latent',
        generator=torch.Generator(device=DEVICE).manual_seed(req.seed),
        cross_attention_kwargs={'concat_conds': conds},
    ).images
    pixels = _vae.decode(latents.to(_vae.dtype) / _vae.config.scaling_factor).sample
    out = (pixels[0].float().permute(1, 2, 0).cpu().numpy() * 127.5 + 127.5).clip(0, 255)
    return Image.fromarray(out.astype(np.uint8))


@app.post('/relight')
def relight(req: Req):
    if not _ready:
        raise HTTPException(503, 'loading')
    with _gpu:
        try:
            img = _relight(req)
        finally:
            # 한 장 끝날 때마다 쥐고 있던 GPU 메모리를 돌려준다. 같은 GPU로 부스 매팅과
            # Windows 화면까지 돌리므로, 쥐고 있으면 VRAM이 차서 컴퓨터 전체가 버벅인다
            if DEVICE == 'cuda':
                torch.cuda.empty_cache()
    buf = io.BytesIO()
    img.save(buf, 'JPEG', quality=94)
    return {'image': base64.b64encode(buf.getvalue()).decode()}


if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='0.0.0.0', port=int(os.environ.get('PORT', 8000)))
