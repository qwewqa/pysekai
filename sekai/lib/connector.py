from enum import IntEnum
from math import ceil, cos, pi
from typing import Literal, assert_never

from sonolus.script.easing import ease_out_cubic
from sonolus.script.effect import Effect, LoopedEffectHandle
from sonolus.script.interval import clamp, lerp, remap_clamped, unlerp_clamped
from sonolus.script.particle import Particle, ParticleHandle
from sonolus.script.quad import QuadLike, Rect
from sonolus.script.record import Record
from sonolus.script.runtime import time
from sonolus.script.sprite import Sprite

from sekai.lib.ease import EaseType, ease
from sekai.lib.effect import Effects
from sekai.lib.layer import (
    LAYER_ACTIVE_SIDE_CONNECTOR,
    LAYER_NOTE_GUIDE,
    LAYER_SLOT_GLOW_EFFECT,
    get_z,
)
from sekai.lib.layout import (
    Layout,
    approach,
    get_alpha,
    iter_slot_lanes,
    layout_circular_effect,
    layout_hitbox,
    layout_linear_effect,
    layout_slide_connector_segment,
    layout_slot_glow_effect,
    transformed_vec_at,
)
from sekai.lib.options import Options
from sekai.lib.particle import Particles
from sekai.lib.skin import (
    ActiveConnectorSprites,
    GuideSprites,
    Skin,
    black_guide_sprites,
    blue_guide_sprites,
    critical_slide_connector_sprites,
    cyan_guide_sprites,
    green_guide_sprites,
    neutral_guide_sprites,
    normal_slide_connector_sprites,
    purple_guide_sprites,
    red_guide_sprites,
    yellow_guide_sprites,
)

CONNECTOR_QUALITY_SCALE = 3.0

CONNECTOR_TRAIL_SPAWN_PERIOD = 0.1
CONNECTOR_SLOT_SPAWN_PERIOD = 0.2


class ConnectorKind(IntEnum):
    NONE = 0

    ACTIVE_NORMAL = 1
    ACTIVE_CRITICAL = 2
    ACTIVE_FAKE_NORMAL = 51
    ACTIVE_FAKE_CRITICAL = 52

    GUIDE_NEUTRAL = 101
    GUIDE_RED = 102
    GUIDE_GREEN = 103
    GUIDE_BLUE = 104
    GUIDE_YELLOW = 105
    GUIDE_PURPLE = 106
    GUIDE_CYAN = 107
    GUIDE_BLACK = 108


ActiveConnectorKind = Literal[
    ConnectorKind.ACTIVE_NORMAL,
    ConnectorKind.ACTIVE_CRITICAL,
    ConnectorKind.ACTIVE_FAKE_NORMAL,
    ConnectorKind.ACTIVE_FAKE_CRITICAL,
]

GuideConnectorKind = Literal[
    ConnectorKind.GUIDE_NEUTRAL,
    ConnectorKind.GUIDE_RED,
    ConnectorKind.GUIDE_GREEN,
    ConnectorKind.GUIDE_BLUE,
    ConnectorKind.GUIDE_YELLOW,
    ConnectorKind.GUIDE_PURPLE,
    ConnectorKind.GUIDE_CYAN,
    ConnectorKind.GUIDE_BLACK,
]


class ConnectorVisualState(IntEnum):
    WAITING = 0
    INACTIVE = 1
    ACTIVE = 2


def get_active_connector_sprites(kind: ActiveConnectorKind) -> ActiveConnectorSprites:
    result = +ActiveConnectorSprites
    match kind:
        case ConnectorKind.ACTIVE_NORMAL | ConnectorKind.ACTIVE_FAKE_NORMAL:
            result @= normal_slide_connector_sprites
        case ConnectorKind.ACTIVE_CRITICAL | ConnectorKind.ACTIVE_FAKE_CRITICAL:
            result @= critical_slide_connector_sprites
        case _:
            assert_never(kind)
    return result


def get_guide_connector_sprites(kind: GuideConnectorKind) -> GuideSprites:
    result = +GuideSprites
    match kind:
        case ConnectorKind.GUIDE_NEUTRAL:
            result @= neutral_guide_sprites
        case ConnectorKind.GUIDE_RED:
            result @= red_guide_sprites
        case ConnectorKind.GUIDE_GREEN:
            result @= green_guide_sprites
        case ConnectorKind.GUIDE_BLUE:
            result @= blue_guide_sprites
        case ConnectorKind.GUIDE_YELLOW:
            result @= yellow_guide_sprites
        case ConnectorKind.GUIDE_PURPLE:
            result @= purple_guide_sprites
        case ConnectorKind.GUIDE_CYAN:
            result @= cyan_guide_sprites
        case ConnectorKind.GUIDE_BLACK:
            result @= black_guide_sprites
        case _:
            assert_never(kind)
    return result


def get_connector_z(kind: ConnectorKind, target_time: float, lane: float) -> float:
    match kind:
        case (
            ConnectorKind.ACTIVE_NORMAL
            | ConnectorKind.ACTIVE_FAKE_NORMAL
            | ConnectorKind.ACTIVE_CRITICAL
            | ConnectorKind.ACTIVE_FAKE_CRITICAL
        ):
            return get_z(
                LAYER_ACTIVE_SIDE_CONNECTOR, time=-target_time, lane=lane, etc=get_active_connector_z_offset(kind)
            )
        case (
            ConnectorKind.GUIDE_NEUTRAL
            | ConnectorKind.GUIDE_RED
            | ConnectorKind.GUIDE_GREEN
            | ConnectorKind.GUIDE_BLUE
            | ConnectorKind.GUIDE_YELLOW
            | ConnectorKind.GUIDE_PURPLE
            | ConnectorKind.GUIDE_CYAN
            | ConnectorKind.GUIDE_BLACK
        ):
            return get_z(LAYER_NOTE_GUIDE, time=-target_time, lane=lane, etc=kind - ConnectorKind.GUIDE_NEUTRAL)
        case ConnectorKind.NONE:
            return 0.0
        case _:
            assert_never(kind)


def get_active_connector_z_offset(kind: ActiveConnectorKind) -> int:
    match kind:
        case ConnectorKind.ACTIVE_NORMAL | ConnectorKind.ACTIVE_FAKE_NORMAL:
            return 1
        case ConnectorKind.ACTIVE_CRITICAL | ConnectorKind.ACTIVE_FAKE_CRITICAL:
            return 0
        case _:
            assert_never(kind)


def get_connector_alpha_option(kind: ConnectorKind) -> float:
    match kind:
        case (
            ConnectorKind.ACTIVE_NORMAL
            | ConnectorKind.ACTIVE_FAKE_NORMAL
            | ConnectorKind.ACTIVE_CRITICAL
            | ConnectorKind.ACTIVE_FAKE_CRITICAL
        ):
            return Options.slide_alpha
        case (
            ConnectorKind.GUIDE_NEUTRAL
            | ConnectorKind.GUIDE_RED
            | ConnectorKind.GUIDE_GREEN
            | ConnectorKind.GUIDE_BLUE
            | ConnectorKind.GUIDE_YELLOW
            | ConnectorKind.GUIDE_PURPLE
            | ConnectorKind.GUIDE_CYAN
            | ConnectorKind.GUIDE_BLACK
        ):
            return Options.guide_alpha
        case ConnectorKind.NONE:
            return 0.0
        case _:
            assert_never(kind)


def get_connector_quality_option(kind: ConnectorKind) -> int:
    match kind:
        case (
            ConnectorKind.ACTIVE_NORMAL
            | ConnectorKind.ACTIVE_FAKE_NORMAL
            | ConnectorKind.ACTIVE_CRITICAL
            | ConnectorKind.ACTIVE_FAKE_CRITICAL
        ):
            return Options.slide_quality
        case (
            ConnectorKind.GUIDE_NEUTRAL
            | ConnectorKind.GUIDE_RED
            | ConnectorKind.GUIDE_GREEN
            | ConnectorKind.GUIDE_BLUE
            | ConnectorKind.GUIDE_YELLOW
            | ConnectorKind.GUIDE_PURPLE
            | ConnectorKind.GUIDE_CYAN
            | ConnectorKind.GUIDE_BLACK
        ):
            return Options.guide_quality
        case ConnectorKind.NONE:
            return 0
        case _:
            assert_never(kind)


def draw_connector(
    kind: ConnectorKind,
    visual_state: ConnectorVisualState,
    ease_type: EaseType,
    head_lane: float,
    head_size: float,
    head_progress: float,
    head_target_time: float,
    tail_lane: float,
    tail_size: float,
    tail_progress: float,
    tail_target_time: float,
    segment_head_target_time: float,
    segment_head_lane: float,
    segment_head_alpha: float,
    segment_tail_target_time: float,
    segment_tail_alpha: float,
):
    if time() < head_target_time and (
        (head_progress < Layout.progress_start and tail_progress < Layout.progress_start)
        or (head_progress > Layout.progress_cutoff and tail_progress > Layout.progress_cutoff)
        or head_progress == tail_progress
    ):
        return

    normal_sprite = Sprite(-1)
    active_sprite = Sprite(-1)
    match kind:
        case (
            ConnectorKind.ACTIVE_NORMAL
            | ConnectorKind.ACTIVE_CRITICAL
            | ConnectorKind.ACTIVE_FAKE_NORMAL
            | ConnectorKind.ACTIVE_FAKE_CRITICAL
        ):
            sprites = get_active_connector_sprites(kind)
            if sprites.custom_available:
                normal_sprite @= sprites.normal
                active_sprite @= sprites.active
            else:
                normal_sprite @= sprites.fallback
        case (
            ConnectorKind.GUIDE_NEUTRAL
            | ConnectorKind.GUIDE_RED
            | ConnectorKind.GUIDE_GREEN
            | ConnectorKind.GUIDE_BLUE
            | ConnectorKind.GUIDE_YELLOW
            | ConnectorKind.GUIDE_PURPLE
            | ConnectorKind.GUIDE_CYAN
            | ConnectorKind.GUIDE_BLACK
        ):
            sprites = get_guide_connector_sprites(kind)
            if sprites.custom_available:
                normal_sprite @= sprites.normal
            else:
                normal_sprite @= sprites.fallback
        case ConnectorKind.NONE:
            return
        case _:
            assert_never(kind)

    match kind:
        case ConnectorKind.ACTIVE_NORMAL | ConnectorKind.ACTIVE_CRITICAL:
            segment_head_alpha = 1.0
            segment_tail_alpha = 1.0
        case ConnectorKind.ACTIVE_FAKE_NORMAL | ConnectorKind.ACTIVE_FAKE_CRITICAL:
            segment_head_alpha = 1.0
            segment_tail_alpha = 1.0
            if visual_state == ConnectorVisualState.INACTIVE:
                visual_state = ConnectorVisualState.ACTIVE
        case (
            ConnectorKind.GUIDE_NEUTRAL
            | ConnectorKind.GUIDE_RED
            | ConnectorKind.GUIDE_GREEN
            | ConnectorKind.GUIDE_BLUE
            | ConnectorKind.GUIDE_YELLOW
            | ConnectorKind.GUIDE_PURPLE
            | ConnectorKind.GUIDE_CYAN
            | ConnectorKind.GUIDE_BLACK
        ):
            visual_state = ConnectorVisualState.WAITING
        case _:
            assert_never(kind)

    head_alpha = remap_clamped(
        segment_head_target_time, segment_tail_target_time, segment_head_alpha, segment_tail_alpha, head_target_time
    )
    tail_alpha = remap_clamped(
        segment_head_target_time, segment_tail_target_time, segment_head_alpha, segment_tail_alpha, tail_target_time
    )

    if time() >= tail_target_time:
        return
    if time() >= head_target_time:
        start_progress = 1.0
        end_progress = clamp(tail_progress, Layout.progress_start, Layout.progress_cutoff)
        head_frac = unlerp_clamped(head_target_time, tail_target_time, time())
        tail_frac = 1.0
        start_frac = head_frac
        end_frac = remap_clamped(start_progress, tail_progress, head_frac, tail_frac, end_progress)
    else:
        start_progress = clamp(head_progress, Layout.progress_start, Layout.progress_cutoff)
        end_progress = clamp(tail_progress, Layout.progress_start, Layout.progress_cutoff)
        start_frac = unlerp_clamped(head_progress, tail_progress, start_progress)
        end_frac = unlerp_clamped(head_progress, tail_progress, end_progress)

    eased_start_frac = ease(ease_type, start_frac)
    eased_end_frac = ease(ease_type, end_frac)
    start_travel = approach(start_progress)
    end_travel = approach(end_progress)
    start_lane = lerp(head_lane, tail_lane, eased_start_frac)
    end_lane = lerp(head_lane, tail_lane, eased_end_frac)
    start_size = max(1e-3, lerp(head_size, tail_size, eased_start_frac))  # Lightweight rendering needs >0 size.
    end_size = max(1e-3, lerp(head_size, tail_size, eased_end_frac))
    start_alpha = lerp(head_alpha, tail_alpha, start_frac)
    end_alpha = lerp(head_alpha, tail_alpha, end_frac)
    start_screen_center = transformed_vec_at(start_lane, start_travel)
    end_screen_center = transformed_vec_at(end_lane, end_travel)
    x_change = (
        max(
            abs((start_lane - start_size) - (end_lane - end_size)),
            abs((start_lane + start_size) - (end_lane + end_size)),
        )
        * max(start_travel, end_travel)
        * Layout.w_scale
    )
    y_change = abs(start_screen_center.y - end_screen_center.y)
    change_scale = min(x_change, y_change)
    if ease_type == EaseType.LINEAR:
        # Linear still curves due to the approach curve, but less, so we need fewer segments.
        change_scale /= 3
    if Options.fade_out:
        change_scale = max(change_scale, y_change)
    change_scale = max(change_scale, abs(start_alpha - end_alpha) * 2)
    quality = get_connector_quality_option(kind)
    segment_count = min(max(1, ceil(quality * change_scale * CONNECTOR_QUALITY_SCALE)), quality)

    z = get_connector_z(kind, segment_head_target_time, segment_head_lane)

    last_travel = start_travel
    last_lane = start_lane
    last_size = start_size
    last_alpha = start_alpha
    last_target_time = lerp(head_target_time, tail_target_time, start_frac)

    for i in range(1, segment_count + 1):
        next_frac = lerp(start_frac, end_frac, i / segment_count)
        next_progress = lerp(start_progress, end_progress, i / segment_count)
        next_travel = approach(next_progress)
        next_lane = lerp(head_lane, tail_lane, ease(ease_type, next_frac))
        next_size = max(1e-3, lerp(head_size, tail_size, ease(ease_type, next_frac)))
        next_alpha = lerp(head_alpha, tail_alpha, next_frac)
        next_target_time = lerp(head_target_time, tail_target_time, next_frac)

        base_a = (
            get_alpha((last_target_time + next_target_time) / 2)
            * (last_alpha + next_alpha)
            / 2
            * get_connector_alpha_option(kind)
        )

        layout = layout_slide_connector_segment(
            start_lane=last_lane,
            start_size=last_size,
            start_travel=last_travel,
            end_lane=next_lane,
            end_size=next_size,
            end_travel=next_travel,
        )

        if visual_state == ConnectorVisualState.ACTIVE and active_sprite.is_available:
            if Options.connector_animation:
                a_modifier = (cos(2 * pi * time()) + 1) / 2
                normal_sprite.draw(layout, z=z, a=base_a * ease_out_cubic(a_modifier))
                active_sprite.draw(layout, z=z, a=base_a * ease_out_cubic(1 - a_modifier))
            else:
                active_sprite.draw(layout, z=z, a=base_a)
        else:
            normal_sprite.draw(layout, z=z, a=base_a * (1 if visual_state != ConnectorVisualState.INACTIVE else 0.5))

        last_travel = next_travel
        last_lane = next_lane
        last_size = next_size
        last_alpha = next_alpha
        last_target_time = next_target_time


class ActiveConnectorInfo(Record):
    visual_lane: float
    visual_size: float
    input_lane: float
    input_size: float
    is_active: bool
    active_start_time: float
    connector_kind: ConnectorKind

    def get_hitbox(self, leniency: float) -> Rect:
        return layout_hitbox(
            self.input_lane - self.input_size - leniency,
            self.input_lane + self.input_size + leniency,
        )


def update_circular_connector_particle(
    handle: ParticleHandle,
    kind: ActiveConnectorKind,
    lane: float,
    replace: bool,
):
    if not Options.note_effect_enabled:
        return
    layout = layout_circular_effect(lane, w=3.5, h=2.1)
    if replace or handle.id == 0:
        particle = +Particle(-1)
        match kind:
            case ConnectorKind.ACTIVE_NORMAL | ConnectorKind.ACTIVE_FAKE_NORMAL:
                particle @= Particles.normal_slide_connector_circular
            case ConnectorKind.ACTIVE_CRITICAL | ConnectorKind.ACTIVE_FAKE_CRITICAL:
                particle @= Particles.critical_slide_connector_circular
            case _:
                assert_never(kind)
        replace_looped_particle(handle, particle, layout, duration=1)
    else:
        update_looped_particle(handle, layout)


def update_linear_connector_particle(
    handle: ParticleHandle,
    kind: ActiveConnectorKind,
    lane: float,
    replace: bool,
):
    if not Options.note_effect_enabled:
        return
    layout = layout_linear_effect(lane, shear=0)
    particle = +Particle
    if replace or handle.id == 0:
        match kind:
            case ConnectorKind.ACTIVE_NORMAL | ConnectorKind.ACTIVE_FAKE_NORMAL:
                particle @= Particles.normal_slide_connector_linear
            case ConnectorKind.ACTIVE_CRITICAL | ConnectorKind.ACTIVE_FAKE_CRITICAL:
                particle @= Particles.critical_slide_connector_linear
            case _:
                assert_never(kind)
        replace_looped_particle(handle, particle, layout, duration=1)
    else:
        update_looped_particle(handle, layout)


def spawn_linear_connector_trail_particle(
    kind: ActiveConnectorKind,
    lane: float,
):
    if not Options.note_effect_enabled:
        return
    layout = layout_linear_effect(lane, shear=0)
    particle = +Particle
    match kind:
        case ConnectorKind.ACTIVE_NORMAL | ConnectorKind.ACTIVE_FAKE_NORMAL:
            particle @= Particles.normal_slide_connector_trail_linear
        case ConnectorKind.ACTIVE_CRITICAL | ConnectorKind.ACTIVE_FAKE_CRITICAL:
            particle @= Particles.critical_slide_connector_trail_linear
        case _:
            assert_never(kind)
    particle.spawn(layout, duration=0.5)


def spawn_connector_slot_particles(
    kind: ActiveConnectorKind,
    lane: float,
    size: float,
):
    particle = +Particle
    match kind:
        case ConnectorKind.ACTIVE_NORMAL | ConnectorKind.ACTIVE_FAKE_NORMAL:
            particle @= Particles.normal_slide_connector_slot_linear
        case ConnectorKind.ACTIVE_CRITICAL | ConnectorKind.ACTIVE_FAKE_CRITICAL:
            particle @= Particles.critical_slide_connector_slot_linear
        case _:
            assert_never(kind)
    for slot_lane in iter_slot_lanes(lane, size):
        layout = layout_linear_effect(slot_lane, shear=0)
        particle.spawn(layout, duration=0.5)


def draw_connector_slot_glow_effect(
    kind: ActiveConnectorKind,
    start_time: float,
    lane: float,
    size: float,
):
    sprite = +Sprite
    match kind:
        case ConnectorKind.ACTIVE_NORMAL | ConnectorKind.ACTIVE_FAKE_NORMAL:
            sprite @= Skin.normal_slide_connector_slot_glow
        case ConnectorKind.ACTIVE_CRITICAL | ConnectorKind.ACTIVE_FAKE_CRITICAL:
            sprite @= Skin.critical_slide_connector_slot_glow
        case _:
            assert_never(kind)
    height = (3.25 + (cos((time() - start_time) * 8 * pi) + 1) / 2) / 4.25
    layout = layout_slot_glow_effect(lane, size, height)
    z = get_z(LAYER_SLOT_GLOW_EFFECT, -start_time, lane)
    a = remap_clamped(start_time, start_time + 0.25, 0.0, 0.3, time())
    sprite.draw(layout, z=z, a=a)


def update_connector_sfx(
    handle: LoopedEffectHandle,
    kind: ActiveConnectorKind,
    replace: bool,
):
    if not Options.sfx_enabled:
        return
    if Options.auto_sfx:
        return
    effect = +Effect
    match kind:
        case ConnectorKind.ACTIVE_NORMAL | ConnectorKind.ACTIVE_FAKE_NORMAL:
            effect @= Effects.normal_hold
        case ConnectorKind.ACTIVE_CRITICAL | ConnectorKind.ACTIVE_FAKE_CRITICAL:
            effect @= Effects.critical_hold
        case _:
            assert_never(kind)
    if replace:
        replace_looped_sfx(handle, effect)
    elif handle.id == 0:
        handle @= effect.loop()


def schedule_connector_sfx(
    kind: ActiveConnectorKind,
    start_time: float,
    end_time: float,
):
    effect = +Effect
    match kind:
        case ConnectorKind.ACTIVE_NORMAL | ConnectorKind.ACTIVE_FAKE_NORMAL:
            effect @= Effects.normal_hold
        case ConnectorKind.ACTIVE_CRITICAL | ConnectorKind.ACTIVE_FAKE_CRITICAL:
            effect @= Effects.critical_hold
        case _:
            assert_never(kind)
    schedule_looped_sfx(effect, start_time, end_time)


def replace_looped_particle(handle: ParticleHandle, particle: Particle, layout: QuadLike, duration: float):
    if handle.id != 0:
        handle.destroy()
    handle @= particle.spawn(layout, duration, loop=True)


def update_looped_particle(handle: ParticleHandle, layout: QuadLike):
    if handle.id != 0:
        handle.move(layout)


def destroy_looped_particle(handle: ParticleHandle):
    if handle.id != 0:
        handle.destroy()
        handle.id = 0


def replace_looped_sfx(handle: LoopedEffectHandle, effect: Effect):
    if handle.id != 0:
        handle.stop()
    handle @= effect.loop()


def destroy_looped_sfx(handle: LoopedEffectHandle):
    if handle.id != 0:
        handle.stop()
        handle.id = 0


def schedule_looped_sfx(effect: Effect, start_time: float, end_time: float):
    effect.schedule_loop(start_time).stop(end_time)
