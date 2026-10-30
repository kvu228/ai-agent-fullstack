# ADR 0001: Continuous Integration với GitHub Actions

- **Trạng thái**: Đã chấp nhận
- **Ngày**: 2026-10-30
- **Liên quan**: `.github/workflows/ci.yml`, `.github/dependabot.yml`, [tutorial/platform/01](../../tutorial/platform/01_ContinuousIntegration.md)

## Bối cảnh

Trước đây lint và test chỉ chạy khi người phát triển nhớ gõ `make lint` / `make test`. Không có gì ngăn một thay đổi làm hỏng test, sai format, hoặc làm Dockerfile không build được lọt vào `main`. Ngoài ra không có ai theo dõi lỗ hổng trong image và trong dependency.

Repo nằm trên GitHub và là public.

## Các quyết định

### 1. Dùng GitHub Actions

| Phương án | Ưu | Nhược |
| --- | --- | --- |
| **GitHub Actions** | Tích hợp sẵn với PR, branch protection, Dependabot; miễn phí không giới hạn phút cho repo public; có cache tích hợp | Gắn với GitHub |
| GitLab CI | Mạnh, self-host được | Phải mirror repo sang GitLab |
| CircleCI / Jenkins | Linh hoạt | Thêm một hệ thống phải quản lý; Jenkins phải tự vận hành server |

Chọn GitHub Actions vì repo đã ở GitHub và không có lý do để thêm một hệ thống nữa.

### 2. Lọc theo đường dẫn bằng một job `changes`, không dùng `on.paths`

PR chỉ sửa frontend thì không cần chạy test backend. Có hai cách:

- `on: pull_request: paths: [...]` ở mức workflow. Khi không có file nào khớp, **cả workflow không chạy**, và nếu check đó là bắt buộc trong branch protection thì PR bị kẹt mãi ở trạng thái "Expected — Waiting for status".
- Một job `changes` (dùng `dorny/paths-filter`) tính ra danh sách phần bị thay đổi; các job sau dùng `if:` để tự skip. Job bị **skip được tính là thành công** với branch protection.

Chọn cách thứ hai. Push lên `main` luôn chạy toàn bộ, để `main` lúc nào cũng được kiểm tra đầy đủ và để tạo cache mà các PR dùng lại (cache tạo trên `main` được PR đọc, ngược lại thì không).

### 3. Một check tổng hợp `CI result` cho branch protection

Branch protection chỉ yêu cầu một check: `CI result`. Job này fail nếu có job nào `failure` hoặc `cancelled`, và coi `skipped` là ổn. Thêm hay đổi tên job sau này không phải sửa cấu hình branch protection.

### 4. CI gọi lại các target của `Makefile`

`make lint` / `make test` được tách thành `lint-backend`, `lint-frontend`, `test-backend`, `test-frontend`. CI gọi đúng các target này, nên lệnh chạy ở máy và trong CI là một. Sửa lệnh lint ở một chỗ thì cả hai cùng đổi.

### 5. Coverage có ngưỡng tối thiểu

Baseline đo được là **85%** (986 statement, 149 chưa được test). Đặt `fail_under = 80` trong `pyproject.toml`: đủ chỗ để không fail vì dao động nhỏ, nhưng chặn được việc thêm nhiều code không có test. Ngưỡng chỉ được phép tăng theo thời gian. Báo cáo coverage được ghi vào job summary của GitHub.

### 6. Trivy: chỉ fail với lỗ hổng HIGH/CRITICAL đã có bản vá

- `severity: CRITICAL,HIGH` + `ignore-unfixed: true`: chỉ chặn những gì **có thể sửa ngay** bằng cách nâng version. Lỗ hổng chưa có bản vá chỉ tạo nhiễu mà không có hành động nào làm được.
- `scanners: vuln`: tắt secret scanner trên image. Lần chạy đầu nó báo 11 "CRITICAL GCP service account" — đều là docstring ví dụ trong SDK `anthropic`, không phải secret thật. Secret trong code của mình do GitHub secret scanning (miễn phí cho repo public) phụ trách.

Lần scan đầu tiên tìm ra và đã sửa:

| Image | Phát hiện | Cách sửa |
| --- | --- | --- |
| backend | `certifi` (CRITICAL), `filelock`, `pypdf` (HIGH) | `uv lock --upgrade-package ...` |
| backend | ~75 lỗ hổng OS (openssl, perl, util-linux…) | Không cần sửa: do image `python:3.12-slim` cũ trong cache local. Build với base image mới (CI luôn pull) thì sạch |
| frontend | 10 lỗ hổng HIGH trong `npm` có sẵn ở `node:22-alpine` | Xoá `npm`/`npx` khỏi stage runtime: server chỉ cần `node` |

### 7. Supply chain của chính CI

- Mọi action được **ghim theo commit SHA** (kèm comment version). Tag như `v4` có thể bị trỏ sang code khác nếu repo của action bị chiếm quyền; SHA thì không đổi được.
- `permissions: contents: read` ở mức workflow; job nào cần thêm thì tự khai báo.
- `persist-credentials: false` khi checkout: token không nằm lại trong `.git/config` cho các bước sau đọc được.
- Dependabot cập nhật SHA của action, dependency Python/npm và base image hằng tuần, với `cooldown` 7 ngày để không cài ngay một bản vừa phát hành (bản bị chèn mã độc thường bị gỡ trong vài ngày).

## Hệ quả

- PR nào cũng có lint + test + build image + scan. Thời gian chạy được ghi trong tutorial (mục "Đo thời gian CI").
- Một lỗ hổng mới được công bố có thể làm CI đỏ dù code không đổi. Đây là chủ ý: cách sửa thường chỉ là merge PR của Dependabot hoặc nâng một package.
- Eval cho agent (todo mục 2) sẽ được thêm thành một job riêng trong workflow này.
