# 2026 성균관대학교 인공지능프로젝트 과제 1: LibHippo

**LibHippo: Permanent Knowledge Library for LLM.**

> [!NOTE]
> 제 개인 프로젝트 중에서는 AI 사용 비중이 엄청나게 높은 프로젝트입니다.
> 과제 치고는 낮을 수도요?

## 중간발표 초안

![중간발표 ppt 사진](<과제1 중간발표 LibHippo.png>)

## 아키텍처

[architecture.md](architecture.md) 참고

## 개발환경 설정

파이썬 패키지 매니저인 uv를 사용하며, 환경은 `uv sync`시 `.venv`에 생성됩니다.

```shell
# uv가 깔려있다 가정하면
uv sync --extra dev # 빌드만 할거면 `--extra dev`
```

## 실행하기

1. `.env.example`을 `.env.local`로 복사하고, API 키를 입력해줍니다.

2. 실행되는 모델을 변경해야 할 경우, [models.py](src/libhippo/config/models.py)를 수정해주세요.
   * 원래 모델의 판별에는 TypeSafe Jev가 사용되었으나, OpenAI Decisions API를 쓰도록 바꿔도 됩니다.
