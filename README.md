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

- 파이썬 패키지 매니저인 uv를 사용하며, 환경은 `uv sync`시 `.venv`에 생성됩니다.
- 프론트엔드 웹의 경우 빌드를 위해서 Node.js가 설치되어 있어야 합니다.

```shell
# uv가 깔려있다 가정하면
uv sync --extra dev # 빌드만 할거면 `--extra dev`

# (선택) 프론트엔드 웹 설정, npm이 깔려있다 가정하면
cd frontend/web && npm install
```

## 실행하기

1. '개발환경 설정'을 해줍니다.

2. `.env.example`을 `.env.local`로 복사하고, API 키와 기타 설정을 입력해줍니다.
   테스트할 때 home dir에 폴더 생기는게 싫으시다면 `LIBHIPPO_CONFIG_DIR`을 잘 수정해주세요.

3. 실행되는 모델을 변경해야 할 경우, [models.py](src/libhippo/config/models.py)를 수정해주세요.
   * 원본 구현체에는 TypeSafe Jev가 사용되었으나, OpenAI Decisions API를 쓰도록 바꿔도 됩니다. 이 경우 `provider="openai", model="gpt-6-luna"`로 설정해주시면 됩니다.

4. 백엔드 서버를 실행해줍니다.
   현재 cwd가 project 폴더로 열리기 때문에 적당한 폴더로 이동해서 열어도 좋습니다. 이 경우 `--directory=...` 옵션으로 이 libhippo 코드 경로를 지정해주세요.

   ```shell
   uv run python -m libhippo.web --port 8080
   ```

5. 프론트엔드 서버를 실행해줍니다. 나오는 사이트에 접속해주시면 됩니다.

   ```shell
   cd frontend/web && npm run dev
   ```
