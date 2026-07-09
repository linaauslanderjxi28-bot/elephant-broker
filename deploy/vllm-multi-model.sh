#!/bin/bash
# vLLM 双模型服务：embedding + reranker 共用一个容器
# 模型目录：/home/openclaw/docker/vllm_cache/Qwen/
docker run -d --name vllm-embedding \
  --network elephantbroker_default --gpus all \
  -v /home/openclaw/docker/vllm_cache/Qwen/Qwen3-Embedding-0.6B:/models/embedding:ro \
  -v /home/openclaw/docker/vllm_cache/Qwen/Qwen3-Reranker-0.6B:/models/reranker:ro \
  -v /home/openclaw/docker/vllm_cache/torch_compile_cache:/root/.cache/vllm/torch_compile_cache \
  -v /home/openclaw/docker/vllm_cache/modelinfos:/root/.cache/vllm/modelinfos \
  -p 8001:8001 -p 8004:8004 --restart unless-stopped --memory 8g \
  opendatalab/mineru:latest \
  bash -c "
vllm serve /models/embedding --host 0.0.0.0 --port 8001 --task embed --max-model-len 16384 --gpu-memory-utilization 0.54 --enforce-eager &
sleep 90
vllm serve /models/reranker --host 0.0.0.0 --port 8004 --task score --max-model-len 4096 --gpu-memory-utilization 0.36 --enforce-eager --hf_overrides '{\"architectures\":[\"Qwen3ForSequenceClassification\"],\"classifier_from_token\":[\"no\",\"yes\"],\"is_original_qwen3_reranker\":true}' &
wait"
