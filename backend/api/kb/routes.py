"""
KB API - 知识库管理
上传(自动解析→分块→向量化→索引)、列表、删除
"""
import logging
import os
import time
import uuid
from typing import List

from fastapi import APIRouter, File, UploadFile, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/kb", tags=["知识库"])


class DocItem(BaseModel):
    doc_id: str
    doc_name: str
    chunk_count: int

class DocListResponse(BaseModel):
    docs: List[DocItem]

class UploadResponse(BaseModel):
    doc_id: str
    doc_name: str
    chunk_count: int
    elapsed: float

class DeleteResponse(BaseModel):
    doc_id: str
    deleted: bool
    chunk_count: int

_rag_components = {}

def init_kb_routes(vector_store, bm25_index, embedding_model, parse_fn, chunker):
    _rag_components["vector_store"] = vector_store
    _rag_components["bm25_index"] = bm25_index
    _rag_components["embedding_model"] = embedding_model
    _rag_components["parser"] = parse_fn
    _rag_components["chunker"] = chunker
    logger.info("KB routes initialized")

def _get(name: str):
    c = _rag_components.get(name)
    if c is None:
        raise HTTPException(status_code=503, detail=f"RAG component '{name}' not initialized")
    return c

@router.post("/upload", response_model=UploadResponse)
async def upload_file(file: UploadFile = File(...)):
    start = time.perf_counter()
    ext = os.path.splitext(file.filename or "")[1].lower()
    from backend.rag.parser import _SUPPORTED_EXTENSIONS
    if ext not in _SUPPORTED_EXTENSIONS:
        raise HTTPException(status_code=400,
                            detail=f"Unsupported type '{ext}'. Supported: {sorted(_SUPPORTED_EXTENSIONS)}")
    doc_id = str(uuid.uuid4())
    # 上传目录与大小上限取自配置（沙箱不允许在工作区内新建目录）
    from backend.config import get_config
    _cfg = get_config()
    upload_cfg = _cfg.get("upload", {})
    upload_dir = upload_cfg.get("docs_dir") or os.path.join(
        os.path.dirname(__file__), "..", "..", "..", "data", "uploads")
    max_bytes = int(upload_cfg.get("max_file_size_mb") or 50) * 1024 * 1024
    os.makedirs(upload_dir, exist_ok=True)
    temp_path = os.path.join(upload_dir, f"{doc_id}{ext}")
    try:
        content = await file.read()
        if len(content) > max_bytes:
            raise HTTPException(status_code=413,
                                detail=f"文件超过大小上限 {max_bytes // (1024 * 1024)} MB")
        with open(temp_path, "wb") as f:
            f.write(content)

        parse_fn = _get("parser")
        parsed = parse_fn(temp_path, doc_id)
        chunker = _get("chunker")
        chunks = chunker.chunk(text=parsed.get_full_text(), doc_id=doc_id,
                                doc_name=parsed.doc_name, pages_info=parsed.pages,
                                sections=parsed.pages)
        if not chunks:
            raise HTTPException(
                status_code=400,
                detail="未能提取到文本（可能为扫描件/图片型文档，需 OCR；或文档为空白）。已跳过，未写入知识库。")

        emb_model = _get("embedding_model")
        texts = [c["text"] for c in chunks]
        embeddings = emb_model.encode(texts)

        _get("vector_store").add_chunks(chunks, embeddings)
        _get("bm25_index").add_chunks(chunks)

        # 落库：documents + chunks。失败则回滚检索侧，避免「可检索但库中无记录」的不一致状态
        try:
            from backend.database import SessionLocal, crud
            db = SessionLocal()
            try:
                crud.add_document(db, doc_id, parsed.doc_name,
                                  ext.lstrip("."), len(chunks))
                crud.add_chunks(db, chunks)
            finally:
                db.close()
        except Exception as db_err:
            logger.error("KB 落库失败，回滚检索侧: %s", db_err, exc_info=True)
            try:
                _get("vector_store").delete_by_doc_id(doc_id)
                _get("bm25_index").remove_by_doc_id(doc_id)
            except Exception as rb_err:
                logger.error("回滚检索侧失败: %s", rb_err)
            raise HTTPException(status_code=500, detail=f"文档入库失败，已回滚: {db_err}")

        elapsed = time.perf_counter() - start
        logger.info("Uploaded doc=%s (%s): %d chunks in %.2fs", doc_id, file.filename, len(chunks), elapsed)
        return UploadResponse(doc_id=doc_id, doc_name=parsed.doc_name,
                               chunk_count=len(chunks), elapsed=round(elapsed, 3))
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Upload failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if os.path.isfile(temp_path):
            os.remove(temp_path)

@router.get("/list", response_model=DocListResponse)
async def list_docs():
    try:
        vs = _get("vector_store")
        stats = vs.get_collection_stats()
        docs = []
        for did in stats.get("doc_ids", []):
            all_data = vs.collection.get(where={"doc_id": did}, include=["metadatas"])
            ids = all_data.get("ids") or []
            metas = all_data.get("metadatas") or []
            doc_name = metas[0].get("doc_name", "") if metas else ""
            docs.append(DocItem(doc_id=did, doc_name=doc_name, chunk_count=len(ids)))
        return DocListResponse(docs=docs)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/{doc_id}", response_model=DeleteResponse)
async def delete_doc(doc_id: str):
    try:
        vs = _get("vector_store")
        all_data = vs.collection.get(where={"doc_id": doc_id}, include=[])
        chunk_count = len(all_data["ids"]) if all_data and all_data.get("ids") else 0
        vs.delete_by_doc_id(doc_id)
        _get("bm25_index").remove_by_doc_id(doc_id)

        # 同步删除 SQLite 中的 documents / chunks 记录
        try:
            from backend.database import SessionLocal, crud
            db = SessionLocal()
            try:
                crud.delete_document(db, doc_id)
            finally:
                db.close()
        except Exception as db_err:
            logger.warning("KB 删除落库失败: %s", db_err)

        return DeleteResponse(doc_id=doc_id, deleted=True, chunk_count=chunk_count)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))