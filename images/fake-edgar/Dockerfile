# The fake EDGAR's image (spec/fake-edgar.md; spec/deployment.md, IMG-5).
# scripts/build_images.py builds it from the files of the commit it tags, so it
# holds data/ as that commit has it, which the fake EDGAR serves (SERVE-1). Its
# base image is named by its tag and its digest (PIN-4); the fake EDGAR uses
# only Python's standard library (decision record 0006).
FROM docker.io/library/python:3.14-alpine@sha256:f6a589d43c42b9e7f7dc67a12d37132491f362859a5d750607710cc56da3bc72

# It writes no bytecode, so its root file system can be read-only.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/srv/src

COPY src/edgar_pipeline/__init__.py /srv/src/edgar_pipeline/__init__.py
COPY src/edgar_pipeline/fake_edgar/ /srv/src/edgar_pipeline/fake_edgar/
COPY data/ /srv/data/

USER 65534:65534
EXPOSE 8443 8080
ENTRYPOINT ["python", "-m", "edgar_pipeline.fake_edgar", "--data", "/srv/data"]
