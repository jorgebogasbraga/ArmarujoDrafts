"""Tests for standard response embed helpers."""

import pytest

from utils.response_embeds import (
    build_response_embed,
    dismiss_thinking,
    error_embed,
    list_embed,
    success_embed,
)


class TestResponseEmbeds:
    def test_error_colour(self):
        embed = error_embed("Something failed")
        assert embed.colour.value == 0xED4245

    def test_success_colour(self):
        embed = success_embed("Done")
        assert embed.colour.value == 0x57F287

    def test_warning_colour(self):
        embed = build_response_embed("warning", "Careful")
        assert embed.colour.value == 0xFEE75C

    def test_list_embed_joins_lines(self):
        embed = list_embed(["Line 1", "Line 2"], title="Items")
        assert embed.title == "Items"
        assert "Line 1" in embed.description
        assert "Line 2" in embed.description


class TestDismissThinking:
    @pytest.mark.asyncio
    async def test_deletes_the_deferred_placeholder(self):
        deleted = []

        class Ctx:
            class response:
                @staticmethod
                def is_done():
                    return True

            async def delete(self):
                deleted.append(True)

        await dismiss_thinking(Ctx())
        assert deleted == [True]

    @pytest.mark.asyncio
    async def test_does_nothing_before_a_response(self):
        class Ctx:
            class response:
                @staticmethod
                def is_done():
                    return False

            async def delete(self):
                raise AssertionError("should not delete before defer")

        await dismiss_thinking(Ctx())

    @pytest.mark.asyncio
    async def test_survives_a_missing_message(self):
        import discord

        class Ctx:
            class response:
                @staticmethod
                def is_done():
                    return True

            async def delete(self):
                class Response:
                    status = 404
                    reason = "Not Found"

                raise discord.NotFound(Response(), "gone")

        await dismiss_thinking(Ctx())
