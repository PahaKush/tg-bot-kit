"""Типизированный FSM: состояние отвечает только «что мы ждём», данные шага — в пейлоаде.

Данные, разложенные по ключам стейта, каждый шаг угадывает заново. Пейлоад — pydantic-модель
под собственным `flow_key`: поля разных сценариев не пересекаются, и пейлоад чужого сценария
не загрузится как свой.
"""

import logging
from typing import ClassVar

from aiogram.fsm.context import FSMContext
from pydantic import BaseModel


class FlowPayload(BaseModel):
    """База для FSM-пейлоадов: наследник объявляет свой `flow_key`."""

    flow_key: ClassVar[str]


class StateHelper:
    """Сохранение и загрузка пейлоада: модель знает свой ключ сама."""

    @staticmethod
    async def save(state: FSMContext, payload: FlowPayload) -> None:
        # mode="json": хранилище пишет данные через json.dumps
        await state.update_data({payload.flow_key: payload.model_dump(mode="json")})

    @staticmethod
    async def load[T: FlowPayload](state: FSMContext, model_cls: type[T]) -> T | None:
        data = await state.get_data()
        raw = data.get(model_cls.flow_key)
        if not raw:
            return None
        try:
            return model_cls.model_validate(raw)
        except Exception as e:
            logging.error("Пейлоад FSM %r не прошёл валидацию: %s", model_cls.flow_key, e)
            return None
