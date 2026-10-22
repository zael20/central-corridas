package __PACOTE__;

import android.Manifest;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.content.pm.ServiceInfo;
import android.location.Location;
import android.location.LocationListener;
import android.location.LocationManager;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.HandlerThread;
import android.os.IBinder;
import android.os.PowerManager;
import androidx.core.app.NotificationCompat;
import androidx.core.content.ContextCompat;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.Locale;

/**
 * Serviço em primeiro plano que lê o GPS e manda a posição do carro para a central,
 * mesmo com a tela desligada ou o app minimizado. Fica uma notificação fixa enquanto roda.
 * A configuração (servidor, carro, código) fica salva no aparelho: se o Android fechar o app,
 * o serviço volta sozinho com a mesma configuração.
 */
public class RastreioServico extends Service implements LocationListener {
    static final String PREFS = "rastreio";
    static final String CANAL = "rastreio";
    static final int NOTIF_ID = 4101;

    // situação atual, lida pela tela (RastreioPlugin.estado)
    static volatile boolean rodando = false;
    static volatile String carro = "";
    static volatile Location ultima = null;
    static volatile long ultimoEnvio = 0, enviados = 0;
    static volatile String ultimoErro = "";

    private String servidor = "", token = "";
    private long intervaloMs = 10000;
    private HandlerThread linha;
    private Handler envio;
    private PowerManager.WakeLock trava;

    @Override
    public IBinder onBind(Intent intent) { return null; }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        SharedPreferences p = getSharedPreferences(PREFS, MODE_PRIVATE);
        servidor = p.getString("servidor", "");
        token = p.getString("token", "");
        carro = p.getString("carro", "");
        intervaloMs = Math.max(3000, p.getLong("intervaloMs", 10000));
        if (servidor.isEmpty() || carro.isEmpty() || !temPermissao()) { pararTudo(); return START_NOT_STICKY; }
        try {
            Notification n = notificacao("Enviando a localização do carro " + carro + " para a central");
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) startForeground(NOTIF_ID, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_LOCATION);
            else startForeground(NOTIF_ID, n);
        } catch (Exception e) {
            // Android 12+ não deixa religar o GPS em segundo plano sem a tela aberta: espera o motorista abrir o app
            ultimoErro = "Abra o app para religar o GPS.";
            pararTudo();
            return START_NOT_STICKY;
        }
        if (!rodando) iniciarGps();
        return START_STICKY;
    }

    private boolean temPermissao() {
        return ContextCompat.checkSelfPermission(this, Manifest.permission.ACCESS_FINE_LOCATION) == PackageManager.PERMISSION_GRANTED
            || ContextCompat.checkSelfPermission(this, Manifest.permission.ACCESS_COARSE_LOCATION) == PackageManager.PERMISSION_GRANTED;
    }

    @SuppressWarnings("MissingPermission")
    private void iniciarGps() {
        rodando = true;
        ultimoErro = "";
        PowerManager pm = (PowerManager) getSystemService(Context.POWER_SERVICE);
        trava = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "corridas:rastreio");
        trava.setReferenceCounted(false);
        trava.acquire();
        linha = new HandlerThread("rastreio-envio");
        linha.start();
        envio = new Handler(linha.getLooper());
        LocationManager lm = (LocationManager) getSystemService(Context.LOCATION_SERVICE);
        long passo = Math.min(intervaloMs, 5000);
        try {
            if (lm.isProviderEnabled(LocationManager.GPS_PROVIDER)) lm.requestLocationUpdates(LocationManager.GPS_PROVIDER, passo, 0, this, linha.getLooper());
            if (lm.isProviderEnabled(LocationManager.NETWORK_PROVIDER)) lm.requestLocationUpdates(LocationManager.NETWORK_PROVIDER, passo, 0, this, linha.getLooper());
            if (!lm.isProviderEnabled(LocationManager.GPS_PROVIDER)) ultimoErro = "O GPS (Localização) do celular está desligado.";
        } catch (Exception e) {
            ultimoErro = "Não consegui ligar o GPS: " + e.getMessage();
        }
        envio.postDelayed(ciclo, 2000);
    }

    // a cada intervalo manda a posição mais recente (também com o carro parado, para a central saber que o celular está vivo)
    private final Runnable ciclo = new Runnable() {
        @Override public void run() {
            Location l = ultima;
            if (l != null && System.currentTimeMillis() - l.getTime() < 120000) enviar(l);
            if (rodando) envio.postDelayed(this, intervaloMs);
        }
    };

    @Override
    public void onLocationChanged(Location l) {
        Location atual = ultima;
        // GPS preciso vence a posição pela rede; a posição pela rede só entra se o GPS sumiu
        if (atual != null && LocationManager.NETWORK_PROVIDER.equals(l.getProvider()) && LocationManager.GPS_PROVIDER.equals(atual.getProvider())
            && l.getTime() - atual.getTime() < 30000) return;
        ultima = l;
    }

    @Override public void onProviderEnabled(String provider) { if (LocationManager.GPS_PROVIDER.equals(provider)) ultimoErro = ""; }
    @Override public void onProviderDisabled(String provider) { if (LocationManager.GPS_PROVIDER.equals(provider)) ultimoErro = "O GPS (Localização) do celular está desligado."; }
    @Override public void onStatusChanged(String provider, int status, Bundle extras) {}

    private void enviar(Location l) {
        HttpURLConnection c = null;
        try {
            String corpo = String.format(Locale.US, "{\"lat\":%.7f,\"lon\":%.7f,\"precisao\":%.1f,\"velocidade\":%s,\"rumo\":%s,\"em\":%d,\"origem\":\"app\"}",
                l.getLatitude(), l.getLongitude(), l.getAccuracy(),
                l.hasSpeed() ? String.format(Locale.US, "%.1f", l.getSpeed() * 3.6f) : "null",
                l.hasBearing() ? String.format(Locale.US, "%.0f", l.getBearing()) : "null", l.getTime());
            c = (HttpURLConnection) new URL(servidor + "/api/motoristas/" + carro + "/gps").openConnection();
            c.setConnectTimeout(8000);
            c.setReadTimeout(8000);
            c.setRequestMethod("POST");
            c.setDoOutput(true);
            c.setRequestProperty("Content-Type", "application/json");
            c.setRequestProperty("X-Acesso", token);
            try (OutputStream o = c.getOutputStream()) { o.write(corpo.getBytes(StandardCharsets.UTF_8)); }
            int cod = c.getResponseCode();
            try (InputStream in = cod < 400 ? c.getInputStream() : c.getErrorStream()) { if (in != null) while (in.read() != -1) { /* descarta */ } }
            if (cod == 401 || cod == 403) ultimoErro = "A central recusou o código de acesso. Entre de novo no app.";
            else if (cod == 404) ultimoErro = "A central não conhece o carro " + carro + ".";
            else if (cod >= 400) ultimoErro = "A central respondeu erro " + cod + ".";
            else {
                ultimoErro = "";
                ultimoEnvio = System.currentTimeMillis();
                enviados++;
            }
        } catch (Exception e) {
            ultimoErro = "Sem conexão com a central (" + e.getClass().getSimpleName() + "). Tentando de novo…";
        } finally {
            if (c != null) c.disconnect();
        }
    }

    private Notification notificacao(String texto) {
        NotificationManager nm = (NotificationManager) getSystemService(Context.NOTIFICATION_SERVICE);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O && nm.getNotificationChannel(CANAL) == null) {
            NotificationChannel ch = new NotificationChannel(CANAL, "Localização do carro", NotificationManager.IMPORTANCE_LOW);
            ch.setDescription("Aparece enquanto o app envia a posição do carro para a central.");
            nm.createNotificationChannel(ch);
        }
        Intent abrir = getPackageManager().getLaunchIntentForPackage(getPackageName());
        PendingIntent pi = PendingIntent.getActivity(this, 0, abrir, PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
        return new NotificationCompat.Builder(this, CANAL)
            .setContentTitle("Pool Motorista em operação")
            .setContentText(texto)
            .setSmallIcon(android.R.drawable.ic_menu_mylocation)
            .setOngoing(true)
            .setContentIntent(pi)
            .setCategory(NotificationCompat.CATEGORY_SERVICE)
            .build();
    }

    private void pararTudo() {
        rodando = false;
        try { ((LocationManager) getSystemService(Context.LOCATION_SERVICE)).removeUpdates(this); } catch (Exception e) { /* nada */ }
        if (envio != null) envio.removeCallbacksAndMessages(null);
        if (linha != null) linha.quitSafely();
        if (trava != null && trava.isHeld()) trava.release();
        stopForeground(Service.STOP_FOREGROUND_REMOVE);
        stopSelf();
    }

    @Override
    public void onDestroy() {
        if (rodando) pararTudo();
        super.onDestroy();
    }
}
