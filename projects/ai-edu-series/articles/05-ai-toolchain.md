---
title: 工欲善其事——AI 工程师的工具链
cover: /home/media/workspace/company/projects/ai-edu-series/articles/assets/cover-05-ai-toolchain.jpg
---

# Phase 0｜工欲善其事——AI 工程师的工具链

> 别人在装环境，你已经跑通了 CI/CD。

---

📖 本系列基于开源项目 [AI Engineering from Scratch](https://github.com/rohitg00/ai-engineering-from-scratch)（503 节课 · 20 阶段 · MIT 协议），用中文重新梳理 AI 全栈知识体系，从数学基础一路写到多智能体集群。

---

**📌 这篇文章聊什么**：从零搭建 AI 开发环境——Python 虚拟环境、GPU 驱动配置、Docker 容器化、Jupyter 最佳实践、Git/DVC 版本管理、Linux 命令行生存指南

**⏱️ 预计阅读**：18 分钟

**🛠️ 涉及技术**：uv · conda · CUDA · cuDNN · Docker · Jupyter · Git · DVC · Linux · tmux · pyproject.toml

**🎯 内容地图**：AI 开发环境四层架构全景认知 + 一键环境诊断脚本（可直接使用）

---

## 🔥 为什么「装环境」值得专门写一篇文章

先讲一个真实场景。

你打开 GitHub，看到一个很棒的 AI 项目——「用 LoRA 微调 Llama 3.1」。你照着 README 一步步来：`pip install torch`，报错 CUDA 版本不匹配；换 conda 装，版本冲突；折腾两小时后终于装好，`python train.py`——`ImportError: transformers>=4.44 required`。你升级 transformers，torch 又崩了。

两个半小时过去了，你还没看到第一行训练日志。

**AI 工程师的日常至少有 30% 耗在环境问题上。** 这不是夸张——Python 虚拟环境、GPU 驱动、容器化工具、数据版本管理，这四层依赖互相交织，任何一个环节出问题，整个链路就断了。

这篇文章的目标很明确：**把环境问题从前置障碍变成肌肉记忆。** 读完你会知道：

- 怎么用 `uv`/`venv`/`conda` 给每个项目建独立环境，彻底告别「装一个包崩八个项目」
- GPU 环境从 0 到 1 的完整链路——CUDA、cuDNN、Docker，每一步该检查什么
- Jupyter 什么场景用、VS Code 什么场景用、终端什么场景用
- Git + DVC 怎么管理代码和模型，不给 GitHub 推 14GB 的 `.safetensors`
- Linux 命令行的 10 个必会操作——你没 GUI 的时候只能靠它们活下来

---

## 🧠 核心概念：AI 开发环境的四层架构

把 AI 开发环境想象成一栋四层楼，每层依赖下面那层：

```
┌─────────────────────────────────────────────┐
│ 第 4 层：AI/ML 库                            │
│ PyTorch · JAX · transformers · diffusers    │
├─────────────────────────────────────────────┤
│ 第 3 层：语言运行时 + 包管理器                │
│ Python 3.11+ · uv · conda · pnpm · cargo    │
├─────────────────────────────────────────────┤
│ 第 2 层：GPU 驱动 + 容器运行时                │
│ CUDA · cuDNN · Docker · NVIDIA Container Toolkit │
├─────────────────────────────────────────────┤
│ 第 1 层：操作系统基础                          │
│ Linux/macOS/WSL2 · Shell · Git · 编辑器       │
└─────────────────────────────────────────────┘
```

**安装顺序从下往上，一层一层来。** 跳过中间一层直接装顶层——比如系统上没有 CUDA 驱动就直接 `pip install torch`——结果是 torch 装上了但 `torch.cuda.is_available()` 返回 `False`。PyTorch 不会报错，它只是默默地用 CPU 跑。你训了一晚上才发现用的是 CPU，那种感觉不必多描述。

### Python 环境管理：conda/venv/uv 不是玄学

AI 项目最痛苦的矛盾：Project A 需要 PyTorch 2.4（CUDA 12.4），Project B 需要 PyTorch 2.1（CUDA 11.8）。全局装一个 torch，两个项目必崩一个。

**解决方案：每个项目一个独立虚拟环境。** 三种工具都能做到，选择取决于你的场景：

| 工具 | 一句话描述 | 典型场景 |
|------|----------|---------|
| **uv** | 10-100x 于 pip 的包管理器，自带虚拟环境和 Python 版本管理 | 主力推荐，新项目首选 |
| **venv** | Python 内置，零额外依赖 | 不能装 uv 的环境（公司管控严格的内网机器） |
| **conda** | 管理 Python 包 + C 库 + CUDA 工具链的「全家桶」 | 需要特定 CUDA 版本、共享集群无法装系统包 |

**uv（推荐）：**

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh   # 安装
uv python install 3.12                              # 安装 Python
cd your-ai-project && uv init                      # 初始化项目（生成 pyproject.toml）
uv add torch numpy matplotlib                       # 安装依赖
```

`uv` 的三个优势：速度快（并行下载 + Rust 实现）、自带 lockfile（`uv.lock`，确保任何人安装都拿到完全相同的版本）、用 `uv run python train.py` 自动在虚拟环境里执行，不用担心忘记 `source .venv/bin/activate`。

**conda（需要 CUDA 全家桶时）：**

```bash
conda create -n myproject python=3.12
conda activate myproject
conda install pytorch torchvision torchaudio pytorch-cuda=12.4 -c pytorch -c nvidia
```

**一条铁律：不要混用 conda 和 pip。** 如果用了 conda 创建环境，就全部用 conda 装包。`pip install` 进 conda 环境是依赖冲突的经典来源——conda 不知道 pip 装了什么东西，后续 conda 操作可能覆盖掉 pip 安装的包。

**pyproject.toml 和 lockfile：**

每个项目都应该有个 `pyproject.toml`——它取代了 `requirements.txt`。把依赖写进去，别人 clone 你的项目后 `uv sync` 就能得到完全一致的环境：

```toml
[project]
name = "ai-engineering-from-scratch"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "numpy>=1.26",
    "matplotlib>=3.8",
    "jupyter>=1.0",
    "scikit-learn>=1.4",
]

[project.optional-dependencies]
torch = ["torch>=2.3", "torchvision>=0.18"]
llm = ["openai>=1.50"]
```

```bash
uv pip install -e ".[torch]"      # 基础包 + PyTorch
uv pip install -e ".[torch,llm]"  # 全部
```

---

### GPU 环境从 0 到 1：CUDA、cuDNN、Docker 一步到位

GPU 驱动的配置是 AI 环境搭建的头号翻车现场。搞清了其实就三步。

**第一步：确认硬件。**

```bash
nvidia-smi
```

输出里看两样东西：GPU 型号（`NVIDIA GeForce RTX 4090`）和 CUDA 驱动版本（`CUDA Version: 12.6`）。**驱动版本必须 ≥ PyTorch 编译时用的 CUDA 版本。** 比如你的驱动是 12.4，那你不能装 CUDA 12.6 编译的 PyTorch。

**第二步：装 PyTorch（带 CUDA 支持）。**

```bash
uv pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124
```

验证：

```python
import torch
print(f"CUDA available: {torch.cuda.is_available()}")
if torch.cuda.is_available():
    print(f"GPU: {torch.cuda.get_device_name(0)}")
    print(f"VRAM: {torch.cuda.get_device_properties(0).total_memory / 1e9:.1f} GB")
```

**第三步（可选）：跑个 CPU vs GPU 对比，感受一下差距。**

```python
import torch, time

size = 5000
a = torch.randn(size, size)
b = torch.randn(size, size)

start = time.time()
c = a @ b
print(f"CPU:   {time.time() - start:.3f}s")

if torch.cuda.is_available():
    a_gpu, b_gpu = a.cuda(), b.cuda()
    torch.cuda.synchronize()
    start = time.time()
    c_gpu = a_gpu @ b_gpu
    torch.cuda.synchronize()
    print(f"GPU:   {time.time() - start:.3f}s")

# 典型输出（RTX 4090）：CPU: 4.2s, GPU: 0.03s, ~140x 加速
```

**没有 GPU 怎么办？** 用 Google Colab。免费 T4 GPU，打开网页就能跑。Runtime → Change runtime type → T4 GPU。虽然 90 分钟不活动会断连，但对学习阶段的实验足够了。

**避坑清单（都是实打实踩过的坑）：**

1. **`nvidia-smi` 显示的 CUDA 版本 ≠ PyTorch 的 CUDA 版本。** `nvidia-smi` 显示的是驱动支持的最高 CUDA 版本，PyTorch 里 `torch.version.cuda` 才是实际编译用的版本。驱动版本必须 ≥ PyTorch CUDA 版本。
2. **PyTorch 默认装 CPU 版。** `pip install torch`（不加 `--index-url`）装的是 CPU-only 版本。一定要从 PyTorch 官网的 CUDA wheel 安装。
3. **cuDNN 版本要和 CUDA 版本匹配。** cuDNN 9.x 对应 CUDA 12.x，cuDNN 8.x 对应 CUDA 11.x。用 conda 装 CUDA toolkit 时，cuDNN 会自动装对。手动装的话注意版本对应。
4. **Docker 里的 GPU 需要 NVIDIA Container Toolkit。** Linux 宿主机上的 Docker 默认看不到 GPU，必须装 `nvidia-container-toolkit` 并重启 Docker daemon。

---

### Jupyter vs VS Code vs 终端：什么时候用哪个

**Jupyter Notebook：AI 开发的「实验台」。** 把代码拆成小单元，跑一段看一段输出，中间可以随时改参数、画图、写注释。AI 论文里的每一个实验，几乎都先在 Jupyter 里跑通，再移植到脚本里。

核心操作：`Shift+Enter` 运行当前 cell；按 `A` 插入上方 cell、`B` 插入下方；`%timeit np.random.randn(10000)` 精确测一块代码的运行时间；`!pip install` 在 notebook 里直接装包。

**VS Code：工程化项目的「主编辑器」。** Jupyter 适合探索，但当你的代码从 50 行膨胀到 5000 行、分成了 15 个 `.py` 文件时，VS Code 的代码跳转、重构、调试、Git 集成会让你效率翻倍。VS Code 也内置了 Jupyter 支持——装 "Jupyter" 扩展后可以直接打开 `.ipynb` 文件。

**终端（SSH）：云 GPU 机器的「唯一界面」。** 你租了一台 Lambda Labs / RunPod / AutoDL 的 GPU 实例，打开只有一个黑框框。没有 Finder、没有 VS Code、没有任何 GUI。你只能靠命令行——`cd ~`、`ls`、`nvidia-smi`、`tmux`、`htop`。这就是为什么要学 Linux 命令行。

**选择逻辑：**

```
「我要搞清楚这个数据集长什么样」→ Jupyter
「我要写一个可复用的训练脚本，跑通后 push 到 GitHub」→ VS Code + .py 文件
「我要在 8 卡 A100 集群上跑 7B 模型微调」→ SSH 终端 + tmux
```

一条经验法则：**在 Jupyter 里探索，在脚本里交付。** 跑通了实验就把核心逻辑抽成 `.py` 文件，提交到 git。

---

### Git + DVC：管理代码和管理模型是两码事

Git 管代码，这没什么新鲜的。`git add` → `git commit` → `git push`。但 AI 项目有个特殊问题：**模型文件动辄几个 G，.safetensors、.bin、.ckpt 不能进 git。**

```bash
# .gitignore
*.bin
*.safetensors
*.pt
*.pth
*.ckpt
*.onnx
models/
data/*.parquet
```

但这只解决了「不提交模型」。如果你想追踪模型的版本——「上个月的微调 checkpoint 和这次的有什么区别」——你需要 **DVC（Data Version Control）**。

```bash
pip install dvc
dvc init
dvc add models/fine-tuned-llama/
git add models/fine-tuned-llama.dvc models/.gitignore
git commit -m "Track fine-tuned model with DVC"

# 配置远程存储（S3/GCS/OSS）
dvc remote add -d storage s3://my-bucket/dvc
dvc push
```

DVC 的思路：大文件本身存到 S3/OSS，git 里只放一个 `.dvc` 指针文件（几十字节）。别人 clone 后 `dvc pull` 就能拉回完整模型。

| 方案 | 适用场景 |
|------|---------|
| `.gitignore` | 个人项目，模型可重新下载 |
| Git LFS | 小团队共享模型权重 |
| DVC | 需要可复现实验，多机器协作 |

---

### Linux for AI 的 10 个必会命令

你大概率是在 macOS 或 Windows 上开发，但早晚有一天你会 SSH 进一台 Ubuntu 的 GPU 实例。以下是那台机器上你不会用 GUI 时，必须能从肌肉记忆里打出来的 10 个操作：

**1. 我在哪？这是什么？**

```bash
pwd       # 当前目录
ls -la    # 当前目录全部文件（含隐藏文件 + 权限）
```

**2. 移动和文件操作**

```bash
cd /path/to/project     # 跳转
mkdir -p a/b/c          # 一口气建嵌套目录
mv old.txt new.txt      # 重命名
rm -rf broken-project/  # ⚠️ 没有回收站，确认路径再回车
```

**3. 读文件和搜索**

```bash
tail -f training.log                # 实时滚动训练日志（Ctrl+C 停止）
grep "loss:" training.log           # 从日志里捞 loss 行
grep -r "learning_rate" ./configs/  # 在指定目录下全局搜索
```

**4. 进程管理**

```bash
htop                  # 交互式进程管理器（比 top 好看）
nvidia-smi            # GPU 显存占用 + 进程。这个每天用 N 次
kill -9 12345         # 强制杀掉卡死的训练进程
```

**5. 磁盘空间（GPU 机器常遇到「磁盘满了」）**

```bash
df -h                 # 每个挂载点的剩余空间
du -sh ~/.cache/      # Hugging Face 模型缓存吃掉多少 GB
du -sh checkpoints/*  # 每个 checkpoint 多大
```

**6. 下载和传输文件**

```bash
wget https://example.com/model.bin                  # 下载大文件
scp user@remote:/data/results.csv .                 # 从远程机器拉文件
rsync -avz --progress ./checkpoints/ user@remote:/data/  # 断点续传大文件夹
```

**7. 软件包管理**

```bash
sudo apt update && sudo apt install -y build-essential git curl tmux htop
```

**8. tmux——让训练在断开 SSH 后继续跑**

```bash
tmux new -s train         # 新建会话
# 在里面启动训练脚本，然后按 Ctrl+B, D 分离
tmux attach -t train      # 重新连回来
```

**这是最重要的一个。** 你 `ssh` 进 GPU 机器，启动一个 12 小时的训练任务，然后关掉笔记本盖子——训练就停了。放进 tmux 里，它就一直跑着。

**9. 权限**

```bash
chmod +x train.sh       # 脚本加执行权限
sudo command             # 临时拿 root 权限
```

**10. 端口和网络**

```bash
curl -s https://api.example.com/health | python3 -m json.tool  # 测 API + 美化 JSON
```

---

### Docker：让「我这能跑」不再是个笑话

「在我机器上能跑」——AI 项目里这句话尤其常见。你的 PyTorch 2.3 + CUDA 12.4，同事的是 PyTorch 2.1 + CUDA 11.8。Docker 把整个环境——OS、CUDA、PyTorch、Python 包——打包成一个镜像，任何机器上 `docker run` 出来的环境一模一样。

**AI 开发中最常用的三种容器模式：**

| 模式 | 镜像内容 | 大小 | 用途 |
|------|---------|------|------|
| 开发容器 | CUDA + PyTorch + Jupyter + 完整工具链 | ~6 GB | 日常开发、实验 |
| 训练容器 | CUDA + PyTorch + 训练脚本（无编辑器） | ~3 GB | GPU 集群批量训练 |
| 推理容器 | 最小化 CUDA Runtime + 模型服务 | ~1.5 GB | 生产环境 API 服务 |

**开发容器 Dockerfile 的核心：**

```dockerfile
FROM nvidia/cuda:12.4.1-devel-ubuntu22.04
# devel 镜像含编译工具（nvcc），装 flash-attn、bitsandbytes 时需要

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3.12 python3.12-dev python3-pip git curl build-essential

RUN python3.12 -m pip install --no-cache-dir \
    torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124

RUN python3.12 -m pip install --no-cache-dir \
    numpy pandas scikit-learn matplotlib jupyter transformers datasets

WORKDIR /workspace
```

运行容器时，一定要挂载卷（volume）——否则容器删了，你的代码和模型全没了：

```bash
docker run --rm -it --gpus all \
    -v $(pwd):/workspace \      # 挂载代码目录
    -v ~/models:/models \       # 挂载模型缓存（14GB 的 Llama 不用反复下载）
    ai-dev bash
```

**没有 GPU 的 Docker？** 去掉 `--gpus all`。PyTorch 会自动退回到 CPU 模式。

---

## ✍️ 手写实现：一键环境诊断脚本

说一千道一万，不如给一个能跑的东西。下面这个脚本——`env_check.py`——能自动诊断你的 AI 开发环境是否就绪。把它放到任何项目里，运行一遍就知道缺什么。

```python
#!/usr/bin/env python3
"""AI 开发环境一键诊断脚本。
检查 Python 版本、虚拟环境、PyTorch/CUDA/GPU 显存、NumPy。
"""

import sys


def check_python():
    """检查 Python 版本 ≥ 3.11"""
    version = sys.version_info
    ok = version >= (3, 11)
    return ok, f"Python {version.major}.{version.minor}.{version.micro}"


def check_venv():
    """检查是否在虚拟环境里"""
    in_venv = sys.prefix != sys.base_prefix
    detail = f"虚拟环境: {sys.prefix}" if in_venv else "全局 Python（建议用虚拟环境）"
    return in_venv, detail


def check_numpy():
    """检查 NumPy 安装和版本"""
    try:
        import numpy as np
        return True, f"NumPy {np.__version__}"
    except ImportError:
        return False, "NumPy 未安装"


def check_pytorch():
    """检查 PyTorch 安装、CUDA 可用性、GPU 显存"""
    try:
        import torch
    except ImportError:
        return False, "PyTorch 未安装"

    results = [f"PyTorch {torch.__version__}"]

    if torch.cuda.is_available():
        gpu_name = torch.cuda.get_device_name(0)
        vram = torch.cuda.get_device_properties(0).total_memory / 1e9
        cuda_ver = torch.version.cuda
        results.append(f"✅ CUDA {cuda_ver} | GPU: {gpu_name} | 显存: {vram:.1f} GB")
        return True, "\n".join(results)
    else:
        results.append("⚠️ CUDA 不可用（PyTorch 将使用 CPU）")
        return True, "\n".join(results)  # PyTorch 装上了，只是没 CUDA，不算失败


def check_disk():
    """检查关键目录的磁盘空间"""
    import shutil
    home = os.path.expanduser("~")
    cache_dir = os.path.join(home, ".cache")
    total, _used, free = shutil.disk_usage(home)
    gb_free = free / 1e9
    ok = gb_free > 10
    return ok, f"可用空间: {gb_free:.1f} GB（{'足够' if ok else '⚠️ 低于 10 GB，模型下载可能失败'}）"


if __name__ == "__main__":
    import os

    checks = [
        ("Python 版本", check_python),
        ("虚拟环境", check_venv),
        ("NumPy", check_numpy),
        ("PyTorch + GPU", check_pytorch),
        ("磁盘空间", check_disk),
    ]

    print("=" * 55)
    print("  🔍 AI 开发环境诊断报告")
    print("=" * 55)

    all_ok = True
    for name, fn in checks:
        try:
            ok, detail = fn()
            status = "✅" if ok else "❌"
            print(f"\n  {status} {name}")
            for line in detail.split("\n"):
                print(f"     {line}")
            if not ok:
                all_ok = False
        except Exception as e:
            print(f"\n  ❌ {name}: 检查失败 — {e}")
            all_ok = False

    print("\n" + "=" * 55)
    if all_ok:
        print("  ✅ 环境就绪，可以开始 AI 工程之旅。")
    else:
        print("  ⚠️ 存在未满足的依赖，请按上方提示修复。")
    print("=" * 55)
```

**这个脚本做了什么？** 它不试图「修复」任何东西——环境问题太复杂，自动修复容易引入新问题。它只做一件事：**快速告诉你哪里不对。** Python 版本不够？没在虚拟环境里？CUDA 没配上？磁盘快满了？一目了然。

**为什么只有五个检查项？** 因为「够用」。Python、虚拟环境、NumPy、PyTorch+GPU、磁盘空间——这五项通过了，Phase 1-12 的 90% 课程都能跑。如果有问题，先看 CUDA 驱动版本，再看 PyTorch 安装索引。

---

## 🚀 拿来就用

### 环境诊断脚本

把上面的 `env_check.py` 保存到你的项目根目录：

```bash
python env_check.py
```

预期输出（正常环境）：

```
=======================================================
  🔍 AI 开发环境诊断报告
=======================================================

  ✅ Python 版本
     Python 3.12.8
  ✅ 虚拟环境
     虚拟环境: /home/user/projects/.venv
  ✅ NumPy
     NumPy 2.1.3
  ✅ PyTorch + GPU
     PyTorch 2.5.1
     ✅ CUDA 12.4 | GPU: NVIDIA GeForce RTX 4090 | 显存: 24.0 GB
  ✅ 磁盘空间
     可用空间: 156.3 GB（足够）

=======================================================
  ✅ 环境就绪，可以开始 AI 工程之旅。
=======================================================
```

### 快速搭建命令速查

```bash
# 1. 安装 Python 包管理器
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. 创建虚拟环境
uv venv && source .venv/bin/activate

# 3. 装 PyTorch（CUDA 12.4）
uv pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124

# 4. 装常用库
uv pip install numpy pandas matplotlib jupyter scikit-learn

# 5. 验证
python env_check.py
```

### 整个 Phase 0 产出物汇总

| 工具 | 用途 | 源课程链接 |
|------|------|-----------|
| `env_check.py` | 一键环境诊断 | [Lesson 01](https://github.com/rohitg00/ai-engineering-from-scratch/tree/main/phases/00-setup-and-tooling/01-dev-environment) |
| `pyproject.toml` | 项目依赖声明 | [Lesson 06](https://github.com/rohitg00/ai-engineering-from-scratch/tree/main/phases/00-setup-and-tooling/06-python-environments) |
| GPU 自检代码 | CUDA/PyTorch/显存三重验证 | [Lesson 03](https://github.com/rohitg00/ai-engineering-from-scratch/tree/main/phases/00-setup-and-tooling/03-gpu-setup-and-cloud) |
| Dockerfile | 可复现 AI 开发容器 | [Lesson 07](https://github.com/rohitg00/ai-engineering-from-scratch/tree/main/phases/00-setup-and-tooling/07-docker-for-ai) |

---

## 🔮 下期预告

环境搭好了，工具链就位了，下一篇我们正式开始写代码。

Phase 1 是整个系列的理论地基：**数学不是拦路虎——AI 必需的数学直觉。** 不需要证明定理，只需要看懂神经网络在做什么——为什么词向量是 768 维而不是 3 维、矩阵乘法为什么就是空间变换、梯度下降为什么不是玄学而是「站在山顶找下山最快的路」。

下一篇，我们扔掉公式恐惧症。

---

📖 本系列基于开源项目 [AI Engineering from Scratch](https://github.com/rohitg00/ai-engineering-from-scratch)（503 节课 · 20 阶段 · MIT 协议），用中文重新梳理 AI 全栈知识体系，从数学基础一路写到多智能体集群。

🔖 收藏本系列，20 周系统掌握 AI 工程。
