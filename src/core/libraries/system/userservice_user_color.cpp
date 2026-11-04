// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <algorithm>
#include "common/logging/log.h"
#include "core/libraries/system/userservice.h"
#include "core/libraries/system/userservice_error.h"
#include "core/user_settings.h"

namespace Libraries::UserService {

s32 PS4_SYSV_ABI sceUserServiceGetUserColor(int user_id, OrbisUserServiceUserColor* color) {
    LOG_DEBUG(Lib_UserService, "called user_id = {}", user_id);
    if (color == nullptr) {
        LOG_ERROR(Lib_UserService, "color is null");
        return ORBIS_USER_SERVICE_ERROR_INVALID_ARGUMENT;
    }
    const auto settings = UserSettingsImpl::GetInstance();
    const auto* user = settings->GetUserManager().GetUserByID(user_id);
    if (user == nullptr) {
        LOG_ERROR(Lib_UserService, "No user found for user_id = {}", user_id);
        return ORBIS_USER_SERVICE_ERROR_NOT_LOGGED_IN;
    }
    s32 color_idx = static_cast<s32>(user->user_color) - 1;
    if (color_idx < 0 || color_idx > 3) {
        color_idx = std::clamp(user->player_index - 1, 0, 3);
    }
    *color = static_cast<OrbisUserServiceUserColor>(color_idx);
    return ORBIS_OK;
}

} // namespace Libraries::UserService
