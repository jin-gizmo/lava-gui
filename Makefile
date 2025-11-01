SHELL:=/bin/bash

# APP is the name used when the app is installed. i.e. the thing you click on.
APP=Lava
# PKG is the name used to identify the install bundle
PKG=lavagui

E=echo -e
# Colour highlight sequences, H = Header, I=Info, _ = Clear
H=\\033[31m
I=\\033[32m
_=\\033[0m

# ------------------------------------------------------------------------------
HIDDEN_PYTHON=$(shell \
	find . -type f -perm -u=x ! -name '*.py' ! -name '*.sh' ! -path './venv/*' \
			! -path './.??*/*' ! -path './doc/*' ! -path './untracked/*' \
			! -path './dist/*' ! -path './*egg-info*' \
		-print0 | xargs -r -0 file | grep 'Python script' | cut -d: -f1)


.PHONY: black help _venv_is_off _venv_is_on _venv update check icons preflight

export PIP_INDEX_URL


# ------------------------------------------------------------------------------
# Bring in platform specific stuff. Windows has an OS environment var.
ifeq ($(OS),Windows_NT)
include etc/Makefile.windows
else
OS=$(shell uname -s)
ifeq ($(OS),Darwin)
include etc/Makefile.macos
else

help:
	@echo
	@echo "Dude, you're on $(OS). You need more help than I can give you."
	@echo

%:
	@echo "Sorry, $(OS) is not supported."
endif
endif

# ------------------------------------------------------------------------------
VERSION=$(shell $(PYTHON) -c 'import tomllib, sys; s=sys.stdin.read(); d=tomllib.loads(s); print(d["project"]["version"])' < pyproject.toml)

ifeq (${VERSION},)
$(error Cannot workout version)
endif

# ------------------------------------------------------------------------------
# Check virtual environment is not active
_venv_is_off:
	@if [ "$$VIRTUAL_ENV" != "" ] ; \
	then \
		echo Deactivate your virtualenv for this operation ; \
		exit 1 ; \
	fi

_venv_is_on:
	@if [ "$$VIRTUAL_ENV" == "" ] ; \
	then \
		echo Activate your virtualenv for this operation ; \
		exit 1 ; \
	fi
	

_git:	.git
	git config core.hooksPath etc/git-hooks

# ------------------------------------------------------------------------------
init: 	  _venv _git

_check_flutter:
	@echo Checking flutter ...
	@flutter doctor
	flutter --disable-analytics
	@echo

_check_flet:
	@( \
		echo "Checking flet ..." ; \
		flet doctor ; \
		echo ; \
	)

black:  _venv_is_on
	black src
	black $(HIDDEN_PYTHON)

check:	_venv_is_on
	etc/git-hooks/pre-commit

clobber:
	$(RM) -r dist
