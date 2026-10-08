from pydantic import BaseModel, Field
from app.core.problems import ProblemException
from uuid import UUID,uuid4
from sqlalchemy import text
async def track_search(session,request,response,principal,query,subject,category,kind,history_id=None):
 if principal and principal.role!='student':return
 if principal:visitor=principal.user_id
 else:
  try:visitor=UUID(request.cookies.get('search_visitor',''))
  except (ValueError,TypeError):
   visitor=uuid4();response.set_cookie('search_visitor',str(visitor),max_age=31536000,httponly=True,samesite='lax',secure=request.url.scheme=='https')
 await session.execute(text("""INSERT INTO app.search_analytics(visitor,registered,is_demo,query,subject,category,kind,imported_history_id)
 VALUES(:visitor,:registered,coalesce((SELECT email LIKE 'demo.student%@example.invalid' FROM app.users WHERE id=:visitor),false),:query,:subject,:category,:kind,:history)
 ON CONFLICT(imported_history_id) DO NOTHING"""),{'visitor':visitor,'registered':principal is not None,'query':query,'subject':subject,'category':category,'kind':kind,'history':history_id})

class SelectedSearchRequest(BaseModel):
    content_id: UUID = Field(alias="contentId")
    analytics: bool = True
    query: str = Field(min_length=1,max_length=200)

async def selected_analytics(session,request,response,principal,payload,history_id=None):
    result=await session.execute(text("SELECT ci.title,s.code AS subject,ci.content_type_code AS category FROM app.content_items ci JOIN app.subjects s ON s.id=ci.subject_id WHERE ci.id=:id AND ci.status='published'"),{'id':payload.content_id})
    row=result.mappings().one_or_none()
    if row is None:raise ProblemException(404,'content_not_found','Материал не найден')
    await track_search(session,request,response,principal,payload.query.strip(),row['subject'],row['category'],'selection',history_id)
