package __PACOTE__;

import android.Manifest;
import android.app.Activity;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.location.Location;
import android.net.Uri;
import android.os.Build;
import android.os.PowerManager;
import android.provider.Settings;
import androidx.core.app.ActivityCompat;
import androidx.core.content.ContextCompat;
import com.getcapacitor.JSObject;
import com.getcapacitor.PermissionState;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;
import com.getcapacitor.annotation.Permission;
import com.getcapacitor.annotation.PermissionCallback;

/**
 * Ponte entre a tela do motorista (JavaScript) e o RastreioServico.
 *   iniciar({ servidor, carro, token, intervalo })  liga o envio da posição (pede a permissão de GPS se faltar)
 *   parar()                                          desliga
 *   estado()                                         o que está acontecendo (para mostrar na tela)
 *   semRestricaoBateria()                            abre o pedido para o Android não pausar o app
 */
@CapacitorPlugin(name = "Rastreio", permissions = {
    @Permission(alias = "localizacao", strings = { Manifest.permission.ACCESS_FINE_LOCATION, Manifest.permission.ACCESS_COARSE_LOCATION })
})
public class RastreioPlugin extends Plugin {

    @PluginMethod
    public void iniciar(PluginCall call) {
        String servidor = call.getString("servidor", ""), carro = call.getString("carro", "");
        if (servidor.isEmpty() || carro.isEmpty()) { call.reject("Faltou o servidor ou o carro."); return; }
        SharedPreferences.Editor e = getContext().getSharedPreferences(RastreioServico.PREFS, Context.MODE_PRIVATE).edit();
        e.putString("servidor", servidor.replaceAll("/+$", ""));
        e.putString("carro", carro);
        e.putString("token", call.getString("token", ""));
        e.putLong("intervaloMs", (long) (call.getDouble("intervalo", 10.0) * 1000));
        e.apply();
        if (getPermissionState("localizacao") != PermissionState.GRANTED) { requestPermissionForAlias("localizacao", call, "depoisDaPermissao"); return; }
        ligar(call);
    }

    @PermissionCallback
    private void depoisDaPermissao(PluginCall call) {
        if (getPermissionState("localizacao") != PermissionState.GRANTED) { call.reject("Sem permissão de localização. Libere em Configurações → Apps → Pool Motorista → Permissões."); return; }
        ligar(call);
    }

    private void ligar(PluginCall call) {
        Activity a = getActivity();
        // Android 13+: sem esta permissão a notificação fixa some (o envio continua, mas o motorista não vê)
        if (Build.VERSION.SDK_INT >= 33 && ContextCompat.checkSelfPermission(a, Manifest.permission.POST_NOTIFICATIONS) != android.content.pm.PackageManager.PERMISSION_GRANTED)
            ActivityCompat.requestPermissions(a, new String[] { Manifest.permission.POST_NOTIFICATIONS }, 4102);
        try {
            ContextCompat.startForegroundService(getContext(), new Intent(getContext(), RastreioServico.class));
            call.resolve(situacao());
        } catch (Exception ex) {
            call.reject("Não consegui ligar o envio da localização: " + ex.getMessage());
        }
    }

    @PluginMethod
    public void parar(PluginCall call) {
        getContext().getSharedPreferences(RastreioServico.PREFS, Context.MODE_PRIVATE).edit().putString("carro", "").apply();
        getContext().stopService(new Intent(getContext(), RastreioServico.class));
        RastreioServico.rodando = false;
        call.resolve(situacao());
    }

    @PluginMethod
    public void estado(PluginCall call) { call.resolve(situacao()); }

    @PluginMethod
    public void semRestricaoBateria(PluginCall call) {
        try {
            Intent i = new Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package:" + getContext().getPackageName()));
            getActivity().startActivity(i);
        } catch (Exception ex) {
            getActivity().startActivity(new Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS));
        }
        call.resolve(situacao());
    }

    private JSObject situacao() {
        JSObject r = new JSObject();
        r.put("ativo", RastreioServico.rodando);
        r.put("carro", RastreioServico.carro);
        r.put("permissao", getPermissionState("localizacao") == PermissionState.GRANTED);
        PowerManager pm = (PowerManager) getContext().getSystemService(Context.POWER_SERVICE);
        r.put("bateriaLivre", pm.isIgnoringBatteryOptimizations(getContext().getPackageName()));
        r.put("ultimoEnvio", RastreioServico.ultimoEnvio);
        r.put("enviados", RastreioServico.enviados);
        r.put("erro", RastreioServico.ultimoErro);
        Location l = RastreioServico.ultima;
        if (l != null) {
            r.put("lat", l.getLatitude());
            r.put("lon", l.getLongitude());
            r.put("precisao", l.getAccuracy());
            r.put("lidaEm", l.getTime());
            if (l.hasSpeed()) r.put("velocidade", l.getSpeed() * 3.6);
        }
        return r;
    }
}
