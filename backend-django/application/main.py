# -*- coding: utf-8 -*-
# @Time    : 2022/5/9 23:15
# @Author  : 臧成龙
# @FileName: api.py
# @Software: PyCharm
from datetime import datetime

from ninja.main import NinjaAPI
from ninja.renderers import JSONRenderer
from ninja.responses import NinjaJSONEncoder

from common.fu_auth import BearerAuth, ApiKey
from core.router import core_router
from scheduler.router import scheduler_router


class MyJsonEncoder(NinjaJSONEncoder):
    def default(self, o):
        if isinstance(o, datetime):
            return o.strftime('%Y-%m-%d %H:%M:%S')
        return super().default(o)


class MyJsonRenderer(JSONRenderer):
    encoder_class = MyJsonEncoder


api = NinjaAPI(auth=[BearerAuth(), ApiKey()], renderer=MyJsonRenderer())

# @api.exception_handler(DjangoValidationError)
# def service_unavailable(request, exc):
#     return api.create_response(
#         request,
#         {"detail": exc.messages},
#         status=422,
#     )


api.add_router('/core', core_router)
api.add_router('/scheduler', scheduler_router)
