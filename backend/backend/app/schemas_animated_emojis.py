"""动画表情接口请求参数模型。"""

from pydantic import BaseModel, ConfigDict, Field, field_validator


class AnimatedEmojiBatchCategoryRequest(BaseModel):
    """动画表情勾选批量修改分类参数。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    ids: list[int] = Field(min_length=1, max_length=100)
    category: str = Field(min_length=1, max_length=100)

    @field_validator("category")
    @classmethod
    def validate_category(cls, value: str) -> str:
        """分类必须为不含换行和制表符的单行文本。"""
        if any(character in value for character in "\r\n\t"):
            raise ValueError("分类不能包含换行或制表符")
        return value
