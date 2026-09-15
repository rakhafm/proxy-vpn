# Makefile proxy-vpn - pembungkus tipis di atas skrip yang sudah ada.
#
# TIDAK menggantikan apa pun: tiap target memanggil skrip/perintah yang sama
# persis seperti di README, cuma memendekkan hafalan. Kalau ragu apa yang
# dijalankan sebuah target, `make -n <target>` mencetaknya tanpa menjalankan.
#
# Semua target destruktif atau yang menyentuh produksi diberi awalan jelas dan
# TIDAK pernah jadi prasyarat target lain - lihat bagian "Berbahaya" di bawah.

SHELL := /usr/bin/env bash
.DEFAULT_GOAL := help

# --- variabel yang bisa ditimpa: make sweep PROV=proton ---------------------
VENV        ?= .venv-pool
PY          := $(VENV)/bin/python3
PIP         := $(VENV)/bin/pip
PROV        ?= pia
GROUP       ?= sea
POOL        ?= 3
SLOT        ?= slot-1
API         ?= http://127.0.0.1:8080
PROBE_IMAGE ?= olx-pool-probe:latest
REGIONS     ?= jakarta
OUT         ?= shot.png

# Jalankan pool/ sebagai modul dari root repo (config.ROOT = direktori ini).
POOL_RUN := $(PY) -m

.PHONY: help
help:  ## Tampilkan daftar target ini
	@echo "proxy-vpn - target yang tersedia:"
	@echo
	@grep -hE '^[a-zA-Z0-9_-]+:.*?## ' $(MAKEFILE_LIST) \
	  | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'
	@echo
	@echo "Variabel: PROV=$(PROV) GROUP=$(GROUP) POOL=$(POOL) SLOT=$(SLOT) API=$(API)"
	@echo "Contoh:   make sweep PROV=proton   |   make bad SLOT=slot-2"

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

$(PY):
	python3 -m venv $(VENV)
	$(PIP) install -q -r pool/requirements.txt

.PHONY: venv
venv: $(PY)  ## Buat .venv-pool + pasang pool/requirements.txt

.PHONY: probe-image
probe-image:  ## Build image probe harian (sekali, sebelum rotasi pertama)
	# --platform sengaja TIDAK diset di sini: pinnya ada di FROM pool/probe/Dockerfile.
	# Docker Desktop gagal mengenali image lokal kalau --platform diset di docker run.
	docker build -t $(PROBE_IMAGE) pool/probe/

.PHONY: setup
setup: venv probe-image  ## venv + image probe sekaligus

# ---------------------------------------------------------------------------
# Uji
# ---------------------------------------------------------------------------

.PHONY: test
test: $(PY)  ## Self-check pool (61 tes, tanpa Docker/jaringan)
	$(POOL_RUN) pool.test_pool -v

.PHONY: test-one
test-one: $(PY)  ## Satu tes: make test-one T=PoolTest.test_next_candidate_skips_failed
	@[ -n "$(T)" ] || { echo "sebutkan T=<NamaKelas.nama_tes>" >&2; exit 1; }
	$(POOL_RUN) pool.test_pool -v $(T)

.PHONY: lint-sh
lint-sh:  ## Cek sintaks semua skrip bash (repo ini tidak punya linter lain)
	@for f in $$(git ls-files '*.sh'); do bash -n "$$f" || exit 1; done
	@echo "sintaks bash: semua skrip bersih"

.PHONY: check
check: lint-sh test  ## lint-sh + test - jalankan sebelum commit

# ---------------------------------------------------------------------------
# Pool manager
# ---------------------------------------------------------------------------

.PHONY: pool
pool: $(PY)  ## Jalankan scheduler + API di :8080 (foreground)
	@echo "slot: PIA_SLOTS/PROTON_SLOTS/PIA_CUSTOM_SLOTS/NORD_SLOTS - lihat pool/README.md"
	$(POOL_RUN) pool.app

.PHONY: rotate
rotate:  ## Picu rotasi harian sekarang, tanpa menunggu jadwal
	curl -fsS -X POST $(API)/jobs/rotate

.PHONY: verify
verify:  ## Picu verifikasi per jam sekarang
	curl -fsS -X POST $(API)/jobs/verify

.PHONY: bad
bad:  ## Lapor satu slot kena deny: make bad SLOT=slot-2
	curl -fsS -X POST $(API)/slots/$(SLOT)/bad

.PHONY: slots probes proxies health
slots:    ## Status semua slot (JSON)
	curl -fsS $(API)/slots
probes:   ## Riwayat vonis probe (JSON)
	curl -fsS $(API)/probes
proxies:  ## Daftar proxy yang terbit - 503 kalau kolam kosong (itu memang disengaja)
	curl -fsS $(API)/proxies.json
health:   ## Health check pool manager
	curl -fsS $(API)/health

# ---------------------------------------------------------------------------
# Jalur manual (butuh Docker, Chrome, npx playwright)
# ---------------------------------------------------------------------------

.PHONY: servers
servers:  ## Daftar server provider: make servers PROV=pia GROUP=sea
	./servers.sh $(PROV) $(GROUP)

.PHONY: servers-refresh
servers-refresh:  ## Segarkan cache servers-<prov>.txt
	./servers.sh $(PROV) -r $(GROUP)

.PHONY: sweep
sweep:  ## Sapu semua server grup -> hasil-<prov>.csv (~25 menit)
	./servers.sh $(PROV) $(GROUP) | xargs ./sweep.sh $(PROV)

.PHONY: run-clean
run-clean:  ## Jalankan server yang lolos di sweep terakhir: make run-clean POOL=3
	./run-clean.sh $(PROV) --pool $(POOL)

.PHONY: shot
shot:  ## Screenshot satu halaman lewat proxy: make shot URL=... OUT=hasil.png
	@[ -n "$(URL)" ] || { echo "sebutkan URL=<alamat>" >&2; exit 1; }
	./shot.sh "$(URL)" "$(OUT)"

.PHONY: test-proxy
test-proxy:  ## Cek proxy gluetun benar keluar lewat VPN
	./test-proxy.sh $(URL)

.PHONY: profiles
profiles:  ## Download profil .ovpn PIA ke pool/vpn-profile: make profiles REGIONS=jakarta
	./pool/get-pia-ovpn.sh -t all --dedup-ip $(REGIONS)

# ---------------------------------------------------------------------------
# Bersih-bersih
# ---------------------------------------------------------------------------

.PHONY: clean
clean:  ## Hapus __pycache__ saja - tidak menyentuh state atau bukti
	find . -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true
	@echo "__pycache__ dibersihkan (pool.db, probe-out/, vpn-profile/ TIDAK disentuh)"

# ---------------------------------------------------------------------------
# Berbahaya - menghapus state/bukti, atau menyentuh VM produksi.
# Sengaja tidak pernah jadi prasyarat target lain; ketik sendiri kalau perlu.
# ---------------------------------------------------------------------------

.PHONY: DANGER-reset-db
DANGER-reset-db:  ## Hapus pool.db - riwayat slot & probe lokal hilang
	@read -p "hapus pool/pool.db? [y/N] " a; [ "$$a" = y ] || exit 1
	rm -f pool/pool.db

.PHONY: DANGER-clean-probe-out
DANGER-clean-probe-out:  ## Hapus seluruh bukti HTML+PNG di pool/probe-out/
	@read -p "hapus semua bukti di pool/probe-out/? [y/N] " a; [ "$$a" = y ] || exit 1
	rm -rf pool/probe-out/*

.PHONY: DANGER-deploy
DANGER-deploy:  ## Salin kode ke VM produksi lewat tsh (langkah lain tetap manual)
	@echo "deploy.sh menyalin kode + build image di VM. Kredensial, systemd unit,"
	@echo "dan crawler-prod-cronjob.yaml TETAP manual - lihat cetakan di akhir skrip."
	@read -p "lanjut deploy ke $${POOL_VM_HOST:-asl-prd-prod-crawler-proxy-2}? [y/N] " a; [ "$$a" = y ] || exit 1
	./pool/deploy/deploy.sh
